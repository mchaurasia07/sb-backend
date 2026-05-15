from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.core.rate_limit import limiter
from app.entity.user import User
from app.model.request.auth import (
    AddPhoneRequest,
    ForgotPasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SignupRequest,
    ValidateEmailRequest,
    ValidatePhoneRequest,
    VerifyEmailOtpRequest,
)
from app.model.response.auth import AuthTokenResponse, GoogleLoginResponse, UserResponse, ValidateResponse
from app.model.response.common import ApiResponse, success_response
from app.service.auth_service import AuthService

router = APIRouter()


@router.post("/signup", response_model=ApiResponse[UserResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def signup(request: Request, payload: SignupRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[UserResponse]:
    data = await AuthService(session).signup(payload)
    return success_response(data, "Signup successful. Please verify your email OTP.")


@router.post("/verify-email-otp", response_model=ApiResponse[UserResponse])
@limiter.limit("10/minute")
async def verify_email_otp(
    request: Request,
    payload: VerifyEmailOtpRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[UserResponse]:
    data = await AuthService(session).verify_email_otp(payload)
    return success_response(data, "Email verified successfully")


@router.post("/login", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("20/minute")
async def login(request: Request, payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[AuthTokenResponse]:
    data = await AuthService(session).login(payload)
    return success_response(data, "Login successful")


@router.post("/google-login", response_model=ApiResponse[GoogleLoginResponse])
@limiter.limit("20/minute")
async def google_login(
    request: Request,
    payload: GoogleLoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GoogleLoginResponse]:
    data = await AuthService(session).google_login(payload)
    return success_response(data, "Google login successful")


@router.post("/add-phone", response_model=ApiResponse[UserResponse])
async def add_phone(
    payload: AddPhoneRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[UserResponse]:
    data = await AuthService(session).add_phone(current_user, payload)
    return success_response(data, "Phone added successfully")


@router.post("/forgot-password", response_model=ApiResponse[None])
@limiter.limit("5/minute")
async def forgot_password(request: Request, payload: ForgotPasswordRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[None]:
    await AuthService(session).forgot_password(payload)
    return success_response(None, "Password reset OTP sent")


@router.post("/reset-password", response_model=ApiResponse[None])
@limiter.limit("5/minute")
async def reset_password(request: Request, payload: ResetPasswordRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[None]:
    await AuthService(session).reset_password(payload)
    return success_response(None, "Password reset successfully")


@router.post("/refresh-token", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("30/minute")
async def refresh_token(
    request: Request,
    payload: RefreshTokenRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[AuthTokenResponse]:
    data = await AuthService(session).refresh_token(payload)
    return success_response(data, "Token refreshed successfully")


@router.post("/logout", response_model=ApiResponse[None])
async def logout(payload: LogoutRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[None]:
    await AuthService(session).logout(payload)
    return success_response(None, "Logout successful")


@router.post("/validate-email", response_model=ApiResponse[ValidateResponse])
@limiter.limit("20/minute")
async def validate_email(request: Request, payload: ValidateEmailRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[ValidateResponse]:
    data = await AuthService(session).validate_email(payload)
    return success_response(data, "Email validation completed")


@router.post("/validate-phone", response_model=ApiResponse[ValidateResponse])
@limiter.limit("20/minute")
async def validate_phone(request: Request, payload: ValidatePhoneRequest, session: AsyncSession = Depends(get_db_session)) -> ApiResponse[ValidateResponse]:
    data = await AuthService(session).validate_phone(payload)
    return success_response(data, "Phone validation completed")
