from fastapi import APIRouter

from app.routes.v1.auth import router as auth_router
from app.routes.v1.child_library import router as child_library_router
from app.routes.v1.children import router as children_router
from app.routes.v1.custom_stories import router as custom_stories_router
from app.routes.v1.generic_audios import router as generic_audios_router
from app.routes.v1.generic_stories import router as generic_stories_router
from app.routes.v1.notifications import router as notifications_router
from app.routes.v1.stories import router as stories_router
from app.routes.v1.story_narration_routes import router as narration_router
from app.routes.v1.workflows import router as workflows_router


class V1Router:
    def __init__(self):
        self.router = APIRouter()
        self.router.include_router(auth_router, prefix="/auth", tags=["Auth"])
        self.router.include_router(child_library_router, prefix="/child-library", tags=["Child Library"])
        self.router.include_router(children_router, prefix="/children", tags=["Children"])
        self.router.include_router(generic_audios_router, prefix="/generic-audios", tags=["Generic Audios"])
        self.router.include_router(generic_stories_router, prefix="/generic-stories", tags=["Generic Stories"])
        self.router.include_router(custom_stories_router, prefix="/custom-stories", tags=["Custom Stories"])
        self.router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
        self.router.include_router(workflows_router, prefix="/workflows", tags=["Workflows"])
        self.router.include_router(stories_router, prefix="/stories", tags=["Stories"])
        self.router.include_router(narration_router, prefix="/stories", tags=["Narration"])


api_router = V1Router().router
