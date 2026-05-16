from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    phone: str | None
    first_name: str | None
    last_name: str | None
    is_email_verified: bool
    is_phone_verified: bool

    model_config = {"from_attributes": True}


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    child_profile_exists: bool


class GoogleLoginResponse(AuthTokenResponse):
    phone_required: bool
    redirect_to: str


class ValidateResponse(BaseModel):
    available: bool
