from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


UUID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("users", "id"),
    ("users", "active_child_profile_id"),
    ("child_profiles", "id"),
    ("child_profiles", "user_id"),
    ("otp_verifications", "id"),
    ("otp_verifications", "user_id"),
    ("refresh_tokens", "id"),
    ("refresh_tokens", "user_id"),
    ("stories", "id"),
    ("stories", "user_id"),
    ("stories", "child_id"),
    ("story_pages", "id"),
    ("story_pages", "story_id"),
    ("story_steps", "id"),
    ("story_steps", "story_id"),
    ("story_contents", "id"),
    ("story_contents", "story_id"),
    ("story_batch_jobs", "id"),
    ("story_batch_jobs", "story_id"),
    ("generic_stories", "id"),
    ("generic_story_contents", "id"),
    ("generic_story_contents", "generic_story_id"),
    ("generic_story_workflows", "id"),
    ("generic_story_workflows", "user_id"),
    ("generic_story_workflows", "generic_story_id"),
    ("generic_story_batch_jobs", "id"),
    ("generic_story_batch_jobs", "generic_story_id"),
    ("generic_story_batch_jobs", "workflow_id"),
    ("custom_story_workflows", "id"),
    ("custom_story_workflows", "user_id"),
    ("custom_story_workflows", "child_id"),
    ("custom_story_workflows", "story_id"),
    ("custom_story_workflow_steps", "id"),
    ("custom_story_workflow_steps", "workflow_id"),
    ("custom_story_batch_jobs", "id"),
    ("custom_story_batch_jobs", "workflow_id"),
    ("custom_story_batch_jobs", "story_id"),
    ("child_books", "id"),
    ("child_books", "child_id"),
    ("child_books", "story_id"),
    ("generic_audios", "id"),
    ("child_audios", "id"),
    ("child_audios", "child_id"),
    ("child_audios", "audio_id"),
    ("child_activity_logs", "id"),
    ("child_activity_logs", "child_id"),
    ("child_activity_logs", "resource_id"),
    ("push_device_tokens", "id"),
    ("push_device_tokens", "user_id"),
    ("push_device_tokens", "child_id"),
    ("notifications", "id"),
    ("notifications", "user_id"),
    ("notifications", "child_id"),
)


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            print(f"ALEMBIC_VERSION={version}")

            for table_name in (
                "custom_story_workflows",
                "custom_story_workflow_steps",
                "custom_story_batch_jobs",
            ):
                exists = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
                        ),
                        {"table_name": table_name},
                    )
                ).scalar()
                print(f"TABLE {table_name}={'exists' if exists else 'missing'}")

            checked = 0
            compact_rows: list[tuple[str, str, int]] = []
            malformed_rows: list[tuple[str, str, int]] = []
            for table_name, column_name in UUID_COLUMNS:
                exists = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name"
                        ),
                        {"table_name": table_name, "column_name": column_name},
                    )
                ).scalar()
                if not exists:
                    continue
                checked += 1
                compact_count = (
                    await conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM `{table_name}` "
                            f"WHERE `{column_name}` IS NOT NULL "
                            f"AND CHAR_LENGTH(`{column_name}`) = 32 "
                            f"AND `{column_name}` NOT LIKE '%-%' "
                            f"AND `{column_name}` REGEXP '^[0-9A-Fa-f]{{32}}$'"
                        )
                    )
                ).scalar()
                malformed_count = (
                    await conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM `{table_name}` "
                            f"WHERE `{column_name}` IS NOT NULL "
                            f"AND NOT ("
                            f"  `{column_name}` REGEXP '^[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'"
                            f")"
                        )
                    )
                ).scalar()
                if compact_count:
                    compact_rows.append((table_name, column_name, int(compact_count)))
                if malformed_count:
                    malformed_rows.append((table_name, column_name, int(malformed_count)))

            print(f"UUID_COLUMNS_CHECKED={checked}")
            print(f"COMPACT_UUID_REMAINING={compact_rows}")
            print(f"MALFORMED_UUID_ROWS={malformed_rows}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
