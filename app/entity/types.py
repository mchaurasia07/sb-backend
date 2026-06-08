from uuid import UUID

from sqlalchemy import CHAR
from sqlalchemy.types import TypeDecorator


class HyphenatedUUID(TypeDecorator):
    """Store UUIDs as hyphenated strings while exposing Python UUID objects."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, UUID):
            return str(value)
        return str(UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))
