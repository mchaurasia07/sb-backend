#!/usr/bin/env python3
"""Test with user's exact payload to debug the failure."""

import asyncio
import sys
import logging
from datetime import datetime, timedelta
from uuid import uuid4

sys.path.insert(0, '.')

# Enable detailed logging
logging.basicConfig(
    level=logging.DEBUG,
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
    """Test with exact user payload."""
    print("\n" + "="*80)
    print("TESTING WITH USER'S EXACT PAYLOAD")
    print("="*80)

    print(f"\nSettings:")
    print(f"  - Mock Mode: {settings.STORY_MOCK_LLM_RESPONSES}")
    print(f"  - Database: {settings.DATABASE_URL[:40]}...")

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
        print(f"✓ User created: {user_id}")

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
            character_image_url="http://example.com/character.png",  # REQUIRED!
            character_metadata={
                "description": "A curious and brave girl with bright eyes"
            },
        )
        session.add(child)
        await session.commit()
        child_id = child.id
        print(f"✓ Child created: {child_id}")
        print(f"  - DOB: {dob}")
        print(f"  - Character Image: {child.character_image_url}")

        # User's exact payload
        payload = StoryGenerationRequest(
            child_id=child_id,
            mode="INPUT_DRIVEN",
            category="adventure",
            learning_goal="courage",
            context="Emma loves exploring and learning about nature. She's curious and brave.",
            skip_image_generation=True,
            skip_validation=False,
        )

        print(f"\n{'='*60}")
        print("PAYLOAD DETAILS")
        print(f"{'='*60}")
        print(f"  - child_id: {payload.child_id}")
        print(f"  - mode: {payload.mode}")
        print(f"  - category: {payload.category}")
        print(f"  - learning_goal: {payload.learning_goal}")
        print(f"  - context: {payload.context[:50]}...")
        print(f"  - skip_image_generation: {payload.skip_image_generation}")
        print(f"  - skip_validation: {payload.skip_validation}")

        print(f"\n{'='*60}")
        print("EXECUTING WORKFLOW")
        print(f"{'='*60}\n")

        # Run workflow
        service = StoryService(session)
        flags = StoryGenerationFlags.from_request(payload)

        try:
            # Create story
            print("Step 1: Creating story record...")
            story_response = await service.generate_story_async(
                user_id=user_id,
                child_id=child_id,
                payload=payload,
                public_base_url="http://localhost:8000",
            )
            print(f"✓ Story created: {story_response.id}")
            print(f"✓ Status: {story_response.status}")
            story_id = story_response.id

            # Execute workflow
            print("\nStep 2: Executing workflow...")
            completed = await service.execute_workflow(story_id, flags)

            print(f"\n{'='*60}")
            print("✓ WORKFLOW COMPLETED SUCCESSFULLY!")
            print(f"{'='*60}")
            print(f"Final Status: {completed.status}")
            print(f"Title: {completed.title}")
            print(f"Moral: {completed.moral}")
            print(f"Summary: {completed.summary[:80]}...")
            print(f"Pages: {len(completed.pages)}")

            # Show audit trail
            print(f"\n{'='*60}")
            print("AUDIT TRAIL")
            print(f"{'='*60}")
            steps = await service.get_story_steps(user_id, story_id)
            for step in steps:
                status_icon = "✓" if step.status == "COMPLETED" else "✗" if step.status == "FAILED" else "⏳"
                print(f"{status_icon} {step.step_name}: {step.status}")
                if step.error_message:
                    print(f"   Error: {step.error_message[:100]}")

        except Exception as e:
            print(f"\n✗ WORKFLOW FAILED")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {str(e)}")
            print(f"\nFull Traceback:")
            import traceback
            traceback.print_exc()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
