from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


DEFAULT_SOURCE_URL = "mysql+asyncmy://app:root@localhost:3306/storybook"
SKIP_TABLES = {"alembic_version"}


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


async def fetch_table_names(connection: AsyncConnection) -> list[str]:
    result = await connection.execute(
        text(
            """
            SELECT TABLE_NAME
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """
        )
    )
    return [row[0] for row in result if row[0] not in SKIP_TABLES]


async def fetch_columns(connection: AsyncConnection, table_name: str) -> list[tuple[str, str]]:
    result = await connection.execute(
        text(
            """
            SELECT COLUMN_NAME, DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
            """
        ),
        {"table_name": table_name},
    )
    return [(row[0], row[1]) for row in result]


async def fetch_counts(connection: AsyncConnection, table_names: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in table_names:
        result = await connection.execute(text(f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"))
        counts[table_name] = int(result.scalar_one())
    return counts


async def copy_table(
    source: AsyncConnection,
    target: AsyncConnection,
    table_name: str,
    batch_size: int,
) -> int:
    column_info = await fetch_columns(source, table_name)
    columns = [name for name, _data_type in column_info]
    json_columns = {name for name, data_type in column_info if data_type == "json"}
    date_columns = {name for name, data_type in column_info if data_type == "date"}
    datetime_columns = {name for name, data_type in column_info if data_type in {"datetime", "timestamp"}}
    if not columns:
        return 0

    quoted_table = quote_identifier(table_name)
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    selected_columns = ", ".join(
        f"CAST({quote_identifier(column)} AS CHAR) AS {quote_identifier(column)}" for column in columns
    )
    value_columns = ", ".join(f":{column}" for column in columns)
    insert_sql = text(f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({value_columns})")

    result = await source.execute(text(f"SELECT {selected_columns} FROM {quoted_table}"))
    all_rows = [
        normalize_row(dict(row), json_columns, date_columns, datetime_columns)
        for row in result.mappings().all()
    ]
    copied = 0
    for index in range(0, len(all_rows), batch_size):
        rows = all_rows[index : index + batch_size]
        await target.execute(insert_sql, rows)
        copied += len(rows)
    return copied


def normalize_row(
    row: dict[str, Any],
    json_columns: set[str],
    date_columns: set[str],
    datetime_columns: set[str],
) -> dict[str, Any]:
    for column in json_columns:
        value = row.get(column)
        if isinstance(value, (dict, list)):
            row[column] = json.dumps(value)
    for column in date_columns:
        value = row.get(column)
        if isinstance(value, str) and value.startswith("0000-00-00"):
            row[column] = "1970-01-01"
    for column in datetime_columns:
        value = row.get(column)
        if isinstance(value, str) and value.startswith("0000-00-00"):
            row[column] = "1970-01-01 00:00:00"
    return row


async def run(args: argparse.Namespace) -> None:
    source_engine = create_async_engine(args.source_url)
    target_engine = create_async_engine(args.target_url)

    try:
        if args.inspect_source_table:
            async with source_engine.connect() as source:
                await inspect_source_table(source, args.inspect_source_table)
            return

        async with source_engine.connect() as source, target_engine.begin() as target:
            if args.compare_table:
                await compare_table(source, target, args.compare_table)
                return

            source_tables = await fetch_table_names(source)
            target_tables = await fetch_table_names(target)
            missing_tables = sorted(set(source_tables) - set(target_tables))
            if missing_tables:
                raise RuntimeError(
                    "Target database is missing tables: "
                    + ", ".join(missing_tables)
                    + ". Run Alembic migrations on the target database first."
                )

            if args.widen_target_varchars:
                await widen_target_columns(source, target, source_tables)
                return

            source_counts = await fetch_counts(source, source_tables)
            target_counts = await fetch_counts(target, source_tables)

            print("Preflight table counts:")
            for table_name in source_tables:
                print(f"  {table_name}: local={source_counts[table_name]} cloud={target_counts[table_name]}")

            target_has_data = any(count > 0 for count in target_counts.values())
            if not args.execute:
                print("Preflight only. Re-run with --execute to copy data.")
                return

            if target_has_data and not args.truncate_target:
                raise RuntimeError(
                    "Target database already has data. Re-run with --truncate-target if you want to replace it."
                )

            await target.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                if args.truncate_target:
                    for table_name in reversed(source_tables):
                        await target.execute(text(f"TRUNCATE TABLE {quote_identifier(table_name)}"))

                print("Copying data:")
                for table_name in source_tables:
                    try:
                        copied = await copy_table(source, target, table_name, args.batch_size)
                    except Exception as exc:
                        print(f"  {table_name}: failed={exc!r}")
                        raise
                    print(f"  {table_name}: copied={copied}")
            finally:
                await target.execute(text("SET FOREIGN_KEY_CHECKS=1"))

        print("Migration complete.")
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


async def compare_table(source: AsyncConnection, target: AsyncConnection, table_name: str) -> None:
    query = text(
        """
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
        """
    )
    print(f"Column comparison for {table_name}:")
    for label, connection in (("local", source), ("cloud", target)):
        print(f"  {label}:")
        result = await connection.execute(query, {"table_name": table_name})
        for column_name, column_type, is_nullable in result:
            print(f"    {column_name}: {column_type} nullable={is_nullable}")

    column_info = await fetch_columns(source, table_name)
    columns = [name for name, _data_type in column_info]
    selected_columns = ", ".join(
        f"CAST({quote_identifier(column)} AS CHAR) AS {quote_identifier(column)}" for column in columns
    )
    result = await source.execute(text(f"SELECT {selected_columns} FROM {quote_identifier(table_name)} LIMIT 1"))
    row = result.mappings().first()
    if row:
        raw_row = dict(row)
        normalized_row = normalize_row(
            raw_row.copy(),
            {name for name, data_type in column_info if data_type in {"json", "longtext"}},
            {name for name, data_type in column_info if data_type == "date"},
            {name for name, data_type in column_info if data_type in {"datetime", "timestamp"}},
        )
        print("  local first-row types:")
        for column in columns:
            print(f"    {column}: raw={type(raw_row[column]).__name__} normalized={type(normalized_row[column]).__name__}")


async def inspect_source_table(source: AsyncConnection, table_name: str) -> None:
    column_info = await fetch_columns(source, table_name)
    columns = [name for name, _data_type in column_info]
    selected_columns = ", ".join(
        f"CAST({quote_identifier(column)} AS CHAR) AS {quote_identifier(column)}" for column in columns
    )
    result = await source.execute(text(f"SELECT {selected_columns} FROM {quote_identifier(table_name)} LIMIT 1"))
    row = result.mappings().first()
    print(f"Source first-row types for {table_name}:")
    if not row:
        print("  no rows")
        return
    raw_row = dict(row)
    normalized_row = normalize_row(
        raw_row.copy(),
        {name for name, data_type in column_info if data_type in {"json", "longtext"} or name.endswith("_json")},
        {name for name, data_type in column_info if data_type == "date"},
        {name for name, data_type in column_info if data_type in {"datetime", "timestamp"}},
    )
    for column in columns:
        print(f"  {column}: raw={type(raw_row[column]).__name__} normalized={type(normalized_row[column]).__name__}")


async def widen_target_columns(
    source: AsyncConnection,
    target: AsyncConnection,
    table_names: Sequence[str],
) -> None:
    column_query = text(
        """
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
        """
    )
    changed = 0
    for table_name in table_names:
        source_rows = await source.execute(column_query, {"table_name": table_name})
        target_rows = await target.execute(column_query, {"table_name": table_name})
        source_columns = {row[0]: row for row in source_rows}
        target_columns = {row[0]: row for row in target_rows}
        for column_name, source_row in source_columns.items():
            target_row = target_columns.get(column_name)
            if not target_row:
                continue
            source_type, source_length = source_row[1], source_row[2]
            target_type, target_length, target_nullable = target_row[1], target_row[2], target_row[3]
            nullable_sql = "NULL" if target_nullable == "YES" else "NOT NULL"
            if (
                source_type == "varchar"
                and target_type == "varchar"
                and source_length
                and target_length
                and int(target_length) < int(source_length)
            ):
                sql = (
                    f"ALTER TABLE {quote_identifier(table_name)} "
                    f"MODIFY COLUMN {quote_identifier(column_name)} VARCHAR({int(source_length)}) {nullable_sql}"
                )
                await target.execute(text(sql))
                print(f"widened {table_name}.{column_name}: {target_length} -> {source_length}")
                changed += 1
            elif source_type == "longtext" and target_type in {"tinytext", "text", "mediumtext"}:
                sql = (
                    f"ALTER TABLE {quote_identifier(table_name)} "
                    f"MODIFY COLUMN {quote_identifier(column_name)} LONGTEXT {nullable_sql}"
                )
                await target.execute(text(sql))
                print(f"widened {table_name}.{column_name}: {target_type} -> longtext")
                changed += 1
    print(f"Target column widening complete. Changed columns: {changed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy all MySQL table data from local DB to cloud DB.")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--target-url", default=settings.DATABASE_URL)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--compare-table")
    parser.add_argument("--inspect-source-table")
    parser.add_argument("--widen-target-varchars", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--truncate-target", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
