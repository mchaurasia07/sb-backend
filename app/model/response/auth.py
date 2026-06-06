from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.entity.user import AuthProvider
from app.model.response.child import ChildProfileResponse


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    phone: str | None
    first_name: str | None
    last_name: str | None
    is_email_verified: bool
    is_phone_verified: bool

    model_config = {"from_attributes": True}


class UserProfileResponse(UserResponse):
    auth_provider: AuthProvider
    is_active: bool
    active_child_profile_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    child_profile_exists: bool


class ChildLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account_type: str = "child"
    child: ChildProfileResponse
    parent_user_id: UUID


class GoogleLoginResponse(AuthTokenResponse):
    phone_required: bool
    first_time_login: bool
    redirect_to: str


class ValidateResponse(BaseModel):
    available: bool
