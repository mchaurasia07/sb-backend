from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.core.database import AsyncSessionLocal
from app.service.generic_story_diagnostics import GenericStoryConsistencyDiagnostics


DEFAULT_STORY_ID = UUID("082d163b-0fa4-4dc4-9b0e-4dd995c76775")


async def _run() -> None:
    parser = argparse.ArgumentParser(description="Read-only generic story consistency diagnostics.")
    parser.add_argument("generic_story_id", nargs="?", type=UUID, default=DEFAULT_STORY_ID)
    parser.add_argument("--language", default="en")
    parser.add_argument("--contact-sheet", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        report = await GenericStoryConsistencyDiagnostics(session).diagnose(
            args.generic_story_id,
            language=args.language,
            include_contact_sheet=args.contact_sheet,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_run())
