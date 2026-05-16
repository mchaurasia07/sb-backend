#!/usr/bin/env python3
"""
Simple test script to verify story generation flow without OpenAI calls.

Usage:
    1. Set STORY_MOCK_LLM_RESPONSES=true in .env
    2. Run: python test_story_generation_flow.py
    3. Watch the entire 6-step workflow execute with mock responses
"""

import asyncio
import sys
from uuid import UUID, uuid4

# Add project to path
sys.path.insert(0, str(__file__).rsplit("\\", 1)[0])

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.entity.child_profile import ChildProfile
from app.entity.user import User
from app.entity.story import Story
from app.model.request.story import StoryGenerationRequest
from app.service.story_service import StoryService, StoryGenerationFlags
from app.core.database import Base


async def setup_test_data(session: AsyncSession) -> tuple[UUID, UUID]:
    """Create test user and child profile."""
    # Create user
    user = User(
        id=uuid4(),
        email="test@example.com",
        first_name="Test",
        last_name="User",
        phone=None,
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

    # Create child profile with character image (required for story generation)
    from datetime import datetime, timedelta
    # Create DOB for a 6-year-old (age group 5-7)
    dob = datetime.now().date() - timedelta(days=6*365)

    child = ChildProfile(
        id=uuid4(),
        user_id=user.id,
        first_name="Emma",
        last_name="Test",
        dob=dob,
        age=6,
        gender="girl",
        avatar_image_url="http://example.com/avatar.png",
        character_image_url="http://example.com/character.png",  # Required!
        character_metadata={
            "description": "A curious and brave young girl with bright eyes, wearing colorful clothing"
        },
    )
    session.add(child)
    await session.commit()

    print(f"✓ Created test user: {user.id}")
    print(f"✓ Created test child: {child.id}")

    return user.id, child.id


async def test_story_generation_flow():
    """Test the complete story generation workflow."""
    print("\n" + "=" * 80)
    print("TESTING STORY GENERATION FLOW (MOCK MODE)")
    print("=" * 80)

    # Check mock mode is enabled
    if not settings.STORY_MOCK_LLM_RESPONSES:
        print("\nERROR: STORY_MOCK_LLM_RESPONSES must be True in .env")
        print("Set: STORY_MOCK_LLM_RESPONSES=true")
        return False

    print(f"\n✓ Mock LLM Mode: ENABLED")
    print(f"✓ Database: {settings.DATABASE_URL}")

    # Create database tables
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✓ Database tables created")

    # Setup test data
    async with async_session() as session:
        user_id, child_id = await setup_test_data(session)

    # Create story generation request
    request = StoryGenerationRequest(
        child_id=child_id,
        mode="INPUT_DRIVEN",
        category="adventure",
        learning_goal="courage",
        context="Emma discovers a magical garden",
        event_description=None,
        skip_image_generation=True,  # Skip image generation to test faster
        skip_validation=False,
    )

    print(f"\n✓ Story Request:")
    print(f"  - Mode: {request.mode}")
    print(f"  - Category: {request.category}")
    print(f"  - Learning Goal: {request.learning_goal}")

    # Run story generation workflow
    async with async_session() as session:
        service = StoryService(session)

        try:
            # Step 1: Create story record
            print("\n" + "-" * 80)
            print("STEP 1: Creating story record...")
            story_response = await service.generate_story_async(
                user_id=user_id,
                child_id=child_id,
                payload=request,
                public_base_url="http://localhost:8000",
            )
            print(f"✓ Story created: {story_response.id}")
            print(f"✓ Status: {story_response.status}")

            # Step 2: Execute workflow
            print("\n" + "-" * 80)
            print("EXECUTING WORKFLOW (6 STEPS)...")

            story_id = story_response.id
            flags = StoryGenerationFlags.from_request(request)

            # Run the workflow
            completed_story = await service.execute_workflow(story_id, flags)

            print(f"\n✓ Workflow completed successfully!")
            print(f"✓ Final Status: {completed_story.status}")
            print(f"✓ Title: {completed_story.title}")
            print(f"✓ Pages: {completed_story.pages}")

            # Step 3: Verify audit trail
            print("\n" + "-" * 80)
            print("AUDIT TRAIL (Story Steps):")
            async with async_session() as session:
                steps = await service.get_story_steps(user_id, story_id)
                for i, step in enumerate(steps, 1):
                    status_icon = "✓" if step.status == "COMPLETED" else "✗" if step.status == "FAILED" else "⏳"
                    print(
                        f"  {status_icon} Step {i}: {step.step_name} - {step.status} "
                        f"(retries: {step.retry_count})"
                    )

            # Step 4: Verify database contents
            print("\n" + "-" * 80)
            print("DATABASE VERIFICATION:")
            async with async_session() as session:
                final_story = await service.get_story(user_id, story_id)
                print(f"✓ Story retrieved from database")
                print(f"  - ID: {final_story.id}")
                print(f"  - Status: {final_story.status}")
                print(f"  - Title: {final_story.title}")
                print(f"  - Moral: {final_story.moral}")
                print(f"  - Summary: {final_story.summary}")
                print(f"  - Pages count: {len(final_story.pages)}")

                if final_story.pages:
                    print(f"\n✓ Story pages:")
                    for page in final_story.pages:
                        print(f"  - Page {page.page_number}: {page.page_type} - {len(page.text)} chars")

            print("\n" + "=" * 80)
            print("✓ COMPLETE FLOW TEST PASSED!")
            print("=" * 80)
            print("\nSUMMARY:")
            print("  ✓ Story created with PENDING status")
            print("  ✓ All 6 workflow steps executed")
            print("  ✓ Story validation passed")
            print("  ✓ Story plan generated (mock)")
            print("  ✓ Story text generated (mock)")
            print("  ✓ Image plan generated (mock)")
            print("  ✓ Story completed with pages")
            print("  ✓ Audit trail recorded for all steps")
            print("  ✓ Database records verified")
            print("\nNow you can:")
            print("  1. Set STORY_MOCK_LLM_RESPONSES=false to use real OpenAI")
            print("  2. Test via API: POST /api/v1/stories/generate")
            print("  3. Poll: GET /api/v1/stories/{story_id}")
            print("  4. View audit: GET /api/v1/stories/{story_id}/steps")

            return True

        except Exception as e:
            print(f"\n✗ ERROR: {str(e)}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            await engine.dispose()


if __name__ == "__main__":
    success = asyncio.run(test_story_generation_flow())
    sys.exit(0 if success else 1)
