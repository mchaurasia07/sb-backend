from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthException, ConflictException, NotFoundException
from app.core.logger import get_logger
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_password,
    hash_secret,
    verify_password,
)
from app.entity.otp_verification import OtpPurpose
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
from app.repository.child_repository import ChildRepository
from app.repository.otp_repository import OtpRepository
from app.repository.refresh_token_repository import RefreshTokenRepository
from app.repository.user_repository import UserRepository
from app.utils.email import email_client
from app.utils.google_oauth import verify_google_id_token

logger = get_logger(__name__)


class AuthService:
    """Authentication use cases."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.otps = OtpRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)
        self.children = ChildRepository(session)

    async def signup(self, payload: SignupRequest) -> UserResponse:
        if await self.users.get_by_email(payload.email):
            raise ConflictException("Email already registered", status.HTTP_409_CONFLICT, "EMAIL_EXISTS")
        if await self.users.get_by_phone(payload.phone):
            raise ConflictException("Phone already registered", status.HTTP_409_CONFLICT, "PHONE_EXISTS")
        user = await self.users.create_local(
            payload.email,
            payload.phone,
            hash_password(payload.password),
            payload.first_name,
            payload.last_name,
        )
        await self._issue_otp(user, OtpPurpose.EMAIL_VERIFICATION)
        logger.info("user_signup_created", user_id=str(user.id), email=user.email)
        return UserResponse.model_validate(user)

    async def verify_email_otp(self, payload: VerifyEmailOtpRequest) -> UserResponse:
        user = await self._get_user_by_email(payload.email)
        await self._verify_otp(user, OtpPurpose.EMAIL_VERIFICATION, payload.otp)
        await self.users.mark_email_verified(user)
        await email_client.send_welcome_email(user.email)
        logger.info("email_verified", user_id=str(user.id))
        return UserResponse.model_validate(user)

    async def login(self, payload: LoginRequest) -> AuthTokenResponse:
        user = await self.users.get_by_email_or_phone(payload.identifier)
        if user is None or user.password_hash is None:
            raise AuthException("Invalid credentials", status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS")
        self._ensure_not_locked(user)
        if not verify_password(payload.password, user.password_hash):
            await self.users.register_failed_login(user)
            await self.session.commit()
            logger.info("login_failed", user_id=str(user.id))
            raise AuthException("Invalid credentials", status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS")
        if not user.is_email_verified:
            raise AuthException("Email is not verified", status.HTTP_403_FORBIDDEN, "EMAIL_NOT_VERIFIED")
        await self.users.clear_failed_logins(user)
        return await self._build_auth_response(user)

    async def google_login(self, payload: GoogleLoginRequest) -> GoogleLoginResponse:
        google_payload = await verify_google_id_token(payload.id_token)
        google_sub = google_payload["sub"]
        email = google_payload["email"]
        if not google_sub or not email:
            raise AuthException("Invalid Google profile", status.HTTP_401_UNAUTHORIZED, "INVALID_GOOGLE_PROFILE")
        user = await self.users.get_by_google_sub(google_sub)
        if user is None:
            user = await self.users.get_by_email(email)
            if user is None:
                first_name, last_name = self._split_google_name(google_payload.get("name"))
                user = await self.users.create_google(
                    email=email,
                    google_sub=google_sub,
                    first_name=first_name,
                    last_name=last_name,
                )
            else:
                user.google_sub = google_sub
                user.is_email_verified = True
                await self.session.flush()
        auth = await self._build_auth_response(user)
        phone_required = user.phone is None
        redirect_to = "create_child_profile" if not auth.child_profile_exists else "dashboard"
        return GoogleLoginResponse(**auth.model_dump(), phone_required=phone_required, redirect_to=redirect_to)

    async def add_phone(self, current_user: User, payload: AddPhoneRequest) -> UserResponse:
        existing = await self.users.get_by_phone(payload.phone)
        if existing and existing.id != current_user.id:
            raise ConflictException("Phone already registered", status.HTTP_409_CONFLICT, "PHONE_EXISTS")
        user = await self.users.add_phone(current_user, payload.phone)
        return UserResponse.model_validate(user)

    async def forgot_password(self, payload: ForgotPasswordRequest) -> None:
        user = await self._get_user_by_email(payload.email)
        await self._issue_otp(user, OtpPurpose.PASSWORD_RESET)
        logger.info("password_reset_requested", user_id=str(user.id))

    async def reset_password(self, payload: ResetPasswordRequest) -> None:
        user = await self._get_user_by_email(payload.email)
        await self._verify_otp(user, OtpPurpose.PASSWORD_RESET, payload.otp)
        await self.users.set_password(user, hash_password(payload.new_password))
        logger.info("password_reset_completed", user_id=str(user.id))

    async def refresh_token(self, payload: RefreshTokenRequest) -> AuthTokenResponse:
        token_payload = decode_token(payload.refresh_token, TokenType.REFRESH)
        token_hash = hash_secret(payload.refresh_token)
        persisted = await self.refresh_tokens.get_valid(token_hash)
        if persisted is None:
            raise AuthException("Refresh token is invalid", status.HTTP_401_UNAUTHORIZED, "INVALID_REFRESH_TOKEN")
        user = await self.users.get_by_id(UUID(token_payload["sub"]))
        if user is None or not user.is_active:
            raise AuthException("User is inactive or not found", status.HTTP_401_UNAUTHORIZED, "INVALID_USER")
        new_refresh_token = create_refresh_token(user.id)
        await self.refresh_tokens.revoke(persisted, hash_secret(new_refresh_token))
        return await self._build_auth_response(user, refresh_token=new_refresh_token)

    async def logout(self, payload: LogoutRequest) -> None:
        token_hash = hash_secret(payload.refresh_token)
        persisted = await self.refresh_tokens.get_valid(token_hash)
        if persisted:
            await self.refresh_tokens.revoke(persisted)

    async def validate_email(self, payload: ValidateEmailRequest) -> ValidateResponse:
        user = await self.users.get_by_email(payload.email)
        if user:
            raise ConflictException("Email already registered", status.HTTP_409_CONFLICT, "EMAIL_EXISTS")
        return ValidateResponse(available=True)

    async def validate_phone(self, payload: ValidatePhoneRequest) -> ValidateResponse:
        user = await self.users.get_by_phone(payload.phone)
        if user:
            raise ConflictException("Phone already registered", status.HTTP_409_CONFLICT, "PHONE_EXISTS")
        return ValidateResponse(available=True)

    async def _build_auth_response(self, user: User, refresh_token: str | None = None) -> AuthTokenResponse:
        access_token = create_access_token(user.id)
        refresh_token = refresh_token or create_refresh_token(user.id)
        token_payload = decode_token(refresh_token, TokenType.REFRESH)
        expires_at = datetime.fromtimestamp(token_payload["exp"], tz=UTC)
        await self.refresh_tokens.create(user.id, hash_secret(refresh_token), expires_at)
        child_exists = await self.children.exists_for_user(user.id)
        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
            child_profile_exists=child_exists,
        )

    async def _issue_otp(self, user: User, purpose: OtpPurpose) -> None:
        otp = generate_otp()
        await self.otps.invalidate_active(user.id, purpose)
        await self.otps.create(
            user_id=user.id,
            purpose=purpose,
            otp_hash=hash_secret(otp),
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
        )
        await email_client.send_otp_email(user.email, otp)

    async def _verify_otp(self, user: User, purpose: OtpPurpose, otp: str) -> None:
        challenge = await self.otps.get_active(user.id, purpose)
        if challenge is None:
            raise AuthException("OTP is invalid or expired", status.HTTP_400_BAD_REQUEST, "INVALID_OTP")
        if challenge.attempts >= 5:
            raise AuthException("OTP attempt limit exceeded", status.HTTP_429_TOO_MANY_REQUESTS, "OTP_ATTEMPTS_EXCEEDED")
        if challenge.otp_hash != hash_secret(otp):
            await self.otps.increment_attempts(challenge)
            await self.session.commit()
            raise AuthException("OTP is invalid or expired", status.HTTP_400_BAD_REQUEST, "INVALID_OTP")
        await self.otps.mark_used(challenge)

    async def _get_user_by_email(self, email: str) -> User:
        user = await self.users.get_by_email(email)
        if user is None:
            raise NotFoundException("User not found", status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND")
        return user

    def _ensure_not_locked(self, user: User) -> None:
        if user.locked_until and user.locked_until > datetime.now(UTC):
            raise AuthException("Account is temporarily locked", status.HTTP_423_LOCKED, "ACCOUNT_LOCKED")

    def _split_google_name(self, full_name: str | None) -> tuple[str | None, str | None]:
        if not full_name:
            return None, None
        parts = full_name.strip().split(maxsplit=1)
        if not parts:
            return None, None
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None
        return first_name, last_name
