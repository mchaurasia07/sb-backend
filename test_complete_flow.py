#!/usr/bin/env python3
"""Complete end-to-end test of story generation with detailed validation."""

import asyncio
import sys
import logging
from datetime import datetime, timedelta
from uuid import uuid4

sys.path.insert(0, '.')

# Configure logging to see all details
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


async def main():
    """Run complete story generation test."""
    print("\n" + "="*80)
    print("COMPLETE END-TO-END STORY GENERATION TEST")
    print("="*80)

    # Verify settings
    print(f"\nSettings Check:")
    print(f"  Mock LLM Responses: {settings.STORY_MOCK_LLM_RESPONSES}")
    print(f"  Text Model: {settings.STORY_TEXT_MODEL}")
    print(f"  Image Model: {settings.STORY_IMAGE_MODEL}")
    print(f"  Database: {settings.DATABASE_URL[:50]}...")

    if not settings.STORY_MOCK_LLM_RESPONSES:
        print("\n⚠️  WARNING: Mock mode is disabled!")
        print("   Set STORY_MOCK_LLM_RESPONSES=true in .env for testing")

    # Setup database
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✓ Database initialized")

    # Create test data
    async with async_session() as session:
        # Create user
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

        # Create child with proper DOB (6 years old = age group 5-7)
        dob = datetime.now().date() - timedelta(days=6*365)
        child = ChildProfile(
            id=uuid4(),
            user_id=user_id,
            first_name="Emma",
            last_name="Test",
            dob=dob,
            age=6,
            gender="girl",
            avatar_image_url="http://example.com/avatar.png",
            character_image_url="http://example.com/character.png",
            character_metadata={"description": "A curious girl"},
        )
        session.add(child)
        await session.commit()
        child_id = child.id

        print(f"✓ Created test user: {user_id}")
        print(f"✓ Created test child: {child_id} (DOB={dob}, age_group should be 5-7)")

        # Create story generation request
        request = StoryGenerationRequest(
            child_id=child_id,
            mode="INPUT_DRIVEN",
            category="adventure",
            learning_goal="courage",
            context="A magical garden",
            skip_image_generation=True,
            skip_validation=False,
        )

        print(f"\n{'='*60}")
        print("EXECUTING WORKFLOW")
        print(f"{'='*60}")

        # Run workflow
        service = StoryService(session)
        flags = StoryGenerationFlags.from_request(request)

        try:
            # Create story and execute workflow
            print("\n1. Creating story record...")
            story_response = await service.generate_story_async(
                user_id=user_id,
                child_id=child_id,
                payload=request,
                public_base_url="http://localhost:8000",
            )
            print(f"   ✓ Story ID: {story_response.id}")
            print(f"   ✓ Status: {story_response.status}")
            story_id = story_response.id

            print("\n2. Executing workflow (6 steps)...")
            completed = await service.execute_workflow(story_id, flags)

            print(f"\n✓ WORKFLOW COMPLETED")
            print(f"  - Final Status: {completed.status}")
            print(f"  - Title: {completed.title}")
            print(f"  - Moral: {completed.moral}")
            print(f"  - Summary: {completed.summary[:100]}...")

            # Get steps for audit trail
            print(f"\n{'='*60}")
            print("AUDIT TRAIL (Story Steps)")
            print(f"{'='*60}")
            steps = await service.get_story_steps(user_id, story_id)
            for i, step in enumerate(steps, 1):
                status_icon = "✓" if step.status == "COMPLETED" else "✗" if step.status == "FAILED" else "⏳"
                print(f"{status_icon} Step {i}: {step.step_name}")
                print(f"  Status: {step.status}, Retries: {step.retry_count}")
                if step.error_message:
                    print(f"  Error: {step.error_message[:100]}")

            # Get final story
            print(f"\n{'='*60}")
            print("FINAL STORY VERIFICATION")
            print(f"{'='*60}")
            final_story = await service.get_story(user_id, story_id)
            print(f"✓ Story retrieved: {final_story.id}")
            print(f"  Pages: {len(final_story.pages)}")
            if final_story.pages:
                for page in final_story.pages[:3]:
                    print(f"    - Page {page.page_number}: {page.page_type} ({len(page.text)} chars)")
                if len(final_story.pages) > 3:
                    print(f"    ... and {len(final_story.pages)-3} more pages")

            print(f"\n{'='*80}")
            print("✓ TEST PASSED - COMPLETE FLOW WORKS!")
            print(f"{'='*80}")

        except Exception as e:
            print(f"\n✗ TEST FAILED")
            print(f"Error: {str(e)}")
            import traceback
            traceback.print_exc()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
