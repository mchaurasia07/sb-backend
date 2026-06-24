from functools import cached_property

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.service.auth_service import AuthService
from app.service.character_service import CharacterService
from app.service.child_activity_service import ChildActivityService
from app.service.child_audio_service import ChildAudioService
from app.service.child_book_service import ChildBookService
from app.service.child_library_service import ChildLibraryService
from app.service.child_service import ChildService
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.generic_audio_service import GenericAudioService
from app.service.generic_story_service import GenericStoryService
from app.service.image_webp_batch_service import ImageWebPBatchService
from app.service.notification_service import NotificationService
from app.service.story_catalog_service import StoryCatalogService
from app.service.story_narration_service import StoryNarrationService
from app.service.story_service import StoryService
from app.service.story_service_batch_service import StoryServiceBatchService
from app.service.story_video_service import StoryVideoService
from app.service.workflow_service import WorkflowService


class RequestContainer:
    """Request-scoped service factory bound to one AsyncSession."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @cached_property
    def auth(self) -> AuthService:
        return AuthService(self.session)

    @cached_property
    def character(self) -> CharacterService:
        return CharacterService(self.session)

    @cached_property
    def child(self) -> ChildService:
        return ChildService(self.session)

    @cached_property
    def child_activity(self) -> ChildActivityService:
        return ChildActivityService(self.session)

    @cached_property
    def child_audio(self) -> ChildAudioService:
        return ChildAudioService(self.session)

    @cached_property
    def child_book(self) -> ChildBookService:
        return ChildBookService(self.session)

    @cached_property
    def child_library(self) -> ChildLibraryService:
        return ChildLibraryService(self.session)

    @cached_property
    def custom_story_workflow(self) -> CustomStoryWorkflowService:
        return CustomStoryWorkflowService(self.session)

    @cached_property
    def generic_audio(self) -> GenericAudioService:
        return GenericAudioService(self.session)

    @cached_property
    def generic_story(self) -> GenericStoryService:
        return GenericStoryService(self.session)

    @cached_property
    def image_webp_batch(self) -> ImageWebPBatchService:
        return ImageWebPBatchService(self.session)

    @cached_property
    def notification(self) -> NotificationService:
        return NotificationService(self.session)

    @cached_property
    def story(self) -> StoryService:
        return StoryService(self.session)

    @cached_property
    def story_batch(self) -> StoryServiceBatchService:
        return StoryServiceBatchService(self.session)

    @cached_property
    def story_catalog(self) -> StoryCatalogService:
        return StoryCatalogService(self.session)

    @cached_property
    def story_narration(self) -> StoryNarrationService:
        return StoryNarrationService(self.session)

    @cached_property
    def story_video(self) -> StoryVideoService:
        return StoryVideoService(self.session)

    @cached_property
    def workflow_service(self) -> WorkflowService:
        return WorkflowService(self.session)


class AppContainer:
    """Process-level dependency factory.

    The app container is a singleton, but database-backed services remain
    request-scoped through RequestContainer so AsyncSession is never shared.
    """

    def request(self, session: AsyncSession) -> RequestContainer:
        return RequestContainer(session)


app_container = AppContainer()


def get_app_container() -> AppContainer:
    return app_container


def get_request_container(
    session: AsyncSession = Depends(get_db_session),
    container: AppContainer = Depends(get_app_container),
) -> RequestContainer:
    return container.request(session)
