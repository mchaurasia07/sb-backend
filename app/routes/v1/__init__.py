from fastapi import APIRouter

from app.routes.v1.auth import router as auth_router
from app.routes.v1.child_library import router as child_library_router
from app.routes.v1.children import router as children_router
from app.routes.v1.custom_stories import router as custom_stories_router
from app.routes.v1.generic_stories import router as generic_stories_router
from app.routes.v1.stories import router as stories_router
from app.routes.v1.story_narration_routes import router as narration_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(child_library_router, prefix="/child-library", tags=["Child Library"])
api_router.include_router(children_router, prefix="/children", tags=["Children"])
api_router.include_router(generic_stories_router, prefix="/generic-stories", tags=["Generic Stories"])
api_router.include_router(custom_stories_router, prefix="/custom-stories", tags=["Custom Stories"])
api_router.include_router(stories_router, prefix="/stories", tags=["Stories"])
api_router.include_router(narration_router, prefix="/stories", tags=["Narration"])
