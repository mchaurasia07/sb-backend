from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.utils.validators import validate_password_strength, validate_phone_number


class SignupRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    email: EmailStr
    phone: str = Field(min_length=8, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return validate_phone_number(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value

    @model_validator(mode="after")
    def passwords_match(self) -> "SignupRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class VerifyEmailOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=4, max_length=12)


class LoginRequest(BaseModel):
    identifier: str = Field(description="Email address, phone number, or child_user_id")
    password: str = Field(min_length=1, max_length=128)
    child_login: bool = Field(default=False, description="Set to true for child profile login")


class ChildLoginRequest(BaseModel):
    child_user_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=1, max_length=128)


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10)


class AddPhoneRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return validate_phone_number(value)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class ValidateEmailRequest(BaseModel):
    email: EmailStr


class ValidatePhoneRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return validate_phone_number(value)
