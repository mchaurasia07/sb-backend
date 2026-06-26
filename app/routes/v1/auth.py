from fastapi import APIRouter, Depends, Request, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.dependencies import get_current_user
from app.core.rate_limit import limiter
from app.entity.user import User
from app.model.request.auth import (
    AddPhoneRequest,
    ChildLoginRequest,
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
from app.model.response.auth import (
    AuthTokenResponse,
    ChildLoginResponse,
    GoogleLoginResponse,
    UserProfileResponse,
    UserResponse,
    ValidateResponse,
)
from app.model.response.common import ApiResponse, success_response


class AuthRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route("/me", self.get_me, methods=["GET"], response_model=ApiResponse[UserProfileResponse])
        self.router.add_api_route(
            "/signup",
            self._rate_limited(self.signup, "10/minute"),
            methods=["POST"],
            response_model=ApiResponse[UserResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/verify-email-otp",
            self._rate_limited(self.verify_email_otp, "10/minute"),
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse],
        )
        self.router.add_api_route(
            "/login",
            self._rate_limited(self.login, "20/minute"),
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse | ChildLoginResponse],
        )
        self.router.add_api_route(
            "/child-login",
            self._rate_limited(self.child_login, "20/minute"),
            methods=["POST"],
            response_model=ApiResponse[ChildLoginResponse],
        )
        self.router.add_api_route(
            "/google-login",
            self._rate_limited(self.google_login, "20/minute"),
            methods=["POST"],
            response_model=ApiResponse[GoogleLoginResponse],
        )
        self.router.add_api_route("/add-phone", self.add_phone, methods=["POST"], response_model=ApiResponse[UserResponse])
        self.router.add_api_route(
            "/forgot-password",
            self._rate_limited(self.forgot_password, "5/minute"),
            methods=["POST"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/reset-password",
            self._rate_limited(self.reset_password, "5/minute"),
            methods=["POST"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/refresh-token",
            self._rate_limited(self.refresh_token, "30/minute"),
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse],
        )
        self.router.add_api_route("/logout", self.logout, methods=["POST"], response_model=ApiResponse[None])
        self.router.add_api_route(
            "/validate-email",
            self._rate_limited(self.validate_email, "20/minute"),
            methods=["POST"],
            response_model=ApiResponse[ValidateResponse],
        )
        self.router.add_api_route(
            "/validate-phone",
            self._rate_limited(self.validate_phone, "20/minute"),
            methods=["POST"],
            response_model=ApiResponse[ValidateResponse],
        )

    @staticmethod
    def _rate_limited(endpoint, limit: str):
        return limiter.limit(limit)(endpoint)

    async def get_me(self, current_user: User = Depends(get_current_user)) -> ApiResponse[UserProfileResponse]:
        data = UserProfileResponse.model_validate(current_user)
        return success_response(data, "User profile fetched successfully")

    async def signup(
        self,
        request: Request,
        payload: SignupRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[UserResponse]:
        data = await container.auth.signup(payload)
        return success_response(data, "Signup successful. Please verify your email OTP.")

    async def verify_email_otp(
        self,
        request: Request,
        payload: VerifyEmailOtpRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[AuthTokenResponse]:
        data = await container.auth.verify_email_otp(payload)
        return success_response(data, "Email verified successfully")

    async def login(
        self,
        request: Request,
        payload: LoginRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[AuthTokenResponse | ChildLoginResponse]:
        data = await container.auth.login(payload)
        message = "Child login successful" if payload.child_login else "Login successful"
        return success_response(data, message)

    async def child_login(
        self,
        request: Request,
        payload: ChildLoginRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildLoginResponse]:
        data = await container.auth.child_login(payload)
        return success_response(data, "Child login successful")

    async def google_login(
        self,
        request: Request,
        payload: GoogleLoginRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GoogleLoginResponse]:
        data = await container.auth.google_login(payload)
        return success_response(data, "Google login successful")

    async def add_phone(
        self,
        payload: AddPhoneRequest,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[UserResponse]:
        data = await container.auth.add_phone(current_user, payload)
        return success_response(data, "Phone added successfully")

    async def forgot_password(
        self,
        request: Request,
        payload: ForgotPasswordRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.auth.forgot_password(payload)
        return success_response(None, "Password reset OTP sent")

    async def reset_password(
        self,
        request: Request,
        payload: ResetPasswordRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.auth.reset_password(payload)
        return success_response(None, "Password reset successfully")

    async def refresh_token(
        self,
        request: Request,
        payload: RefreshTokenRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[AuthTokenResponse]:
        data = await container.auth.refresh_token(payload)
        return success_response(data, "Token refreshed successfully")

    async def logout(
        self,
        payload: LogoutRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.auth.logout(payload)
        return success_response(None, "Logout successful")

    async def validate_email(
        self,
        request: Request,
        payload: ValidateEmailRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ValidateResponse]:
        data = await container.auth.validate_email(payload)
        return success_response(data, "Email validation completed")

    async def validate_phone(
        self,
        request: Request,
        payload: ValidatePhoneRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ValidateResponse]:
        data = await container.auth.validate_phone(payload)
        return success_response(data, "Phone validation completed")


router = AuthRouter(app_container).router
