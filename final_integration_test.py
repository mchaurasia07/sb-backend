#!/usr/bin/env python3
"""Final integration test with your exact payload for all age groups."""

import asyncio
import sys
import logging
from datetime import datetime, timedelta
from uuid import uuid4

sys.path.insert(0, '.')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings
from app.core.database import Base
from app.entity.child_profile import ChildProfile
from app.entity.user import User
from app.model.request.story import StoryGenerationRequest
from app.service.story_service import StoryService, StoryGenerationFlags


async def test_age_group(session, user_id, age_years, age_group_expected):
    """Test story generation for a specific age group."""
    print(f"\n{'='*70}")
    print(f"Testing: {age_years} year old (Expected age_group: {age_group_expected})")
    print(f"{'='*70}")

    # Create child
    dob = datetime.now().date() - timedelta(days=age_years*365)
    child = ChildProfile(
        id=uuid4(),
        user_id=user_id,
        first_name="Emma",
        last_name="Test",
        dob=dob,
        age=age_years,
        gender="girl",
        avatar_image_url="http://example.com/avatar.png",
        character_image_url="http://example.com/character.png",
        character_metadata={"description": "A curious girl"},
    )
    session.add(child)
    await session.flush()
    child_id = child.id

    # Create request with your exact payload
    request = StoryGenerationRequest(
        child_id=child_id,
        mode="INPUT_DRIVEN",
        category="adventure",
        learning_goal="courage",
        context="Emma loves exploring and learning about nature. She's curious and brave.",
        skip_image_generation=True,
        skip_validation=False,
    )

    service = StoryService(session)
    flags = StoryGenerationFlags.from_request(request)

    try:
        # Create story
        story_response = await service.generate_story_async(
            user_id=user_id,
            child_id=child_id,
            payload=request,
            public_base_url="http://localhost:8000",
        )
        story_id = story_response.id
        print(f"✓ Story created: {story_id}")

        # Execute workflow
        completed = await service.execute_workflow(story_id, flags)

        print(f"\n✓ WORKFLOW COMPLETED")
        print(f"  - Status: {completed.status}")
        print(f"  - Title: {completed.title}")
        print(f"  - Pages: {len(completed.pages)}")

        # Get steps to verify all passed
        steps = await service.get_story_steps(user_id, story_id)
        all_completed = all(s.status == "COMPLETED" for s in steps)

        if all_completed:
            print(f"  - ✓ All steps completed successfully")
        else:
            print(f"  - ✗ Some steps failed:")
            for step in steps:
                if step.status != "COMPLETED":
                    print(f"    - {step.step_name}: {step.status}")
                    if step.error_message:
                        print(f"      Error: {step.error_message[:100]}")
            return False

        return True

    except Exception as e:
        print(f"\n✗ WORKFLOW FAILED")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Test all age groups."""
    print("\n" + "="*70)
    print("FINAL INTEGRATION TEST - ALL AGE GROUPS")
    print("="*70)

    print(f"\nSettings:")
    print(f"  - Mock Mode: {settings.STORY_MOCK_LLM_RESPONSES}")
    print(f"  - Database: {settings.DATABASE_URL[:40]}...")

    # Setup database
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✓ Database initialized")

    # Create user
    async with async_session() as session:
        user = User(
            id=uuid4(),
            email="test@test.com",
            first_name="Test",
            last_name="User",
            password_hash="dummy",
            is_email_verified=True,
            is_phone_verified=False,
            failed_login_attempts=0,
            is_account_locked=False,
            active_child_profile_id=None,
            auth_provider="local",
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        print(f"✓ Test user created: {user_id}")

        # Test each age group
        test_cases = [
            (3, "2-4", 6),      # Toddler: 6 pages
            (6, "5-7", 8),      # Early Reader: 8 pages
            (10, "8-12", 12),   # Advanced: 12 pages
        ]

        results = []
        for age_years, age_group, expected_pages in test_cases:
            success = await test_age_group(session, user_id, age_years, age_group)
            results.append((age_years, age_group, success))
            await session.commit()

    # Summary
    print(f"\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}")

    for age_years, age_group, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status} Age {age_years} ({age_group})")

    if all(r[2] for r in results):
        print(f"\n{'='*70}")
        print("✓ ALL TESTS PASSED - WORKFLOW COMPLETE FOR ALL AGE GROUPS!")
        print(f"{'='*70}")
        return True
    else:
        print(f"\n{'='*70}")
        print("✗ SOME TESTS FAILED")
        print(f"{'='*70}")
        return False

    await engine.dispose()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
