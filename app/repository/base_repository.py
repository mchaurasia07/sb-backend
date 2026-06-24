from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

ModelT = TypeVar("ModelT")


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


class BaseRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession, model: type[ModelT]):
        self.session = session
        self.model = model

    async def create(self, **kwargs: Any) -> ModelT:
        entity = self.model(**kwargs)
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def get_by_id(self, entity_id: Any, *, for_update: bool = False) -> ModelT | None:
        statement = select(self.model).where(self.model.id == entity_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_one(
        self,
        *,
        filters: Sequence[Any] = (),
        for_update: bool = False,
    ) -> ModelT | None:
        statement = select(self.model).where(*filters)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list(
        self,
        filters: Sequence[Any] = (),
        order_by: Sequence[Any] = (),
        load_columns: Sequence[Any] | None = None,
    ) -> list[ModelT]:
        statement = select(self.model).where(*filters)
        order_by_items = _as_tuple(order_by)
        if order_by_items:
            statement = statement.order_by(*order_by_items)
        if load_columns:
            statement = statement.options(load_only(*load_columns))

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update(
        self,
        entity: ModelT,
        *,
        flag_modified_fields: Iterable[str] = (),
    ) -> ModelT:
        for field_name in flag_modified_fields:
            if getattr(entity, field_name, None) is not None:
                flag_modified(entity, field_name)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def list_paginated(
        self,
        *,
        filters: Sequence[Any] = (),
        page: int,
        page_size: int,
        order_by: Sequence[Any] = (),
        load_columns: Sequence[Any] | None = None,
    ) -> tuple[list[ModelT], int]:
        total = await self.session.scalar(select(func.count()).select_from(self.model).where(*filters))
        id_statement = (
            select(self.model.id)
            .where(*filters)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        order_by_items = _as_tuple(order_by)
        if order_by_items:
            id_statement = id_statement.order_by(*order_by_items)

        id_result = await self.session.execute(id_statement)
        entity_ids = list(id_result.scalars().all())
        if not entity_ids:
            return [], int(total or 0)

        entity_statement = select(self.model).where(self.model.id.in_(entity_ids))
        if load_columns:
            entity_statement = entity_statement.options(load_only(*load_columns))

        result = await self.session.execute(entity_statement)
        entities_by_id = {entity.id: entity for entity in result.scalars().all()}
        return [entities_by_id[entity_id] for entity_id in entity_ids if entity_id in entities_by_id], int(total or 0)
