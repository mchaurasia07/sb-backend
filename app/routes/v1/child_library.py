from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AuthException
from app.entity.generic_audio import GenericAudioLanguage
from app.model.response.child_audio import ChildAudioResponse
from app.model.response.child_library import ChildLibraryBookResponse
from app.model.response.common import ApiResponse, PaginatedResponse, success_response


def require_child_context(auth: AuthContext) -> AuthContext:
    if not auth.is_child or auth.child_id is None:
        raise AuthException("Child access required", status_code=403, code="CHILD_ACCESS_REQUIRED")
    return auth


class ChildLibraryRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "/generic-stories",
            self.list_child_generic_books,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildLibraryBookResponse]],
        )
        self.router.add_api_route(
            "/custom-stories",
            self.list_child_custom_books,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildLibraryBookResponse]],
        )
        self.router.add_api_route(
            "/audios",
            self.list_child_audios,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildAudioResponse]],
        )
        self.router.add_api_route(
            "/audios/{child_audio_id}",
            self.get_child_audio,
            methods=["GET"],
            response_model=ApiResponse[ChildAudioResponse],
        )

    async def list_child_generic_books(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildLibraryBookResponse]]:
        child_auth = require_child_context(auth)
        data = await container.child_library.list_generic_books(
            child_id=child_auth.child_id,
            page=page,
            page_size=page_size,
        )
        return success_response(data, "Child generic books retrieved successfully")

    async def list_child_custom_books(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildLibraryBookResponse]]:
        child_auth = require_child_context(auth)
        data = await container.child_library.list_custom_books(
            child_id=child_auth.child_id,
            page=page,
            page_size=page_size,
        )
        return success_response(data, "Child custom books retrieved successfully")

    async def list_child_audios(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        language: GenericAudioLanguage | None = Query(default=None),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildAudioResponse]]:
        child_auth = require_child_context(auth)
        data = await container.child_audio.list_for_child_library(
            child_id=child_auth.child_id,
            page=page,
            page_size=page_size,
            language=language,
        )
        return success_response(data, "Child audios retrieved successfully")

    async def get_child_audio(
        self,
        child_audio_id: UUID,
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildAudioResponse]:
        child_auth = require_child_context(auth)
        data = await container.child_audio.get_for_child_library(
            child_id=child_auth.child_id,
            child_audio_id=child_audio_id,
        )
        return success_response(data, "Child audio retrieved successfully")


router = ChildLibraryRouter(app_container).router
