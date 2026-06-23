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
            self.signup,
            methods=["POST"],
            response_model=ApiResponse[UserResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/verify-email-otp",
            self.verify_email_otp,
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse],
        )
        self.router.add_api_route(
            "/login",
            self.login,
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse | ChildLoginResponse],
        )
        self.router.add_api_route(
            "/child-login",
            self.child_login,
            methods=["POST"],
            response_model=ApiResponse[ChildLoginResponse],
        )
        self.router.add_api_route(
            "/google-login",
            self.google_login,
            methods=["POST"],
            response_model=ApiResponse[GoogleLoginResponse],
        )
        self.router.add_api_route("/add-phone", self.add_phone, methods=["POST"], response_model=ApiResponse[UserResponse])
        self.router.add_api_route(
            "/forgot-password",
            self.forgot_password,
            methods=["POST"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/reset-password",
            self.reset_password,
            methods=["POST"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/refresh-token",
            self.refresh_token,
            methods=["POST"],
            response_model=ApiResponse[AuthTokenResponse],
        )
        self.router.add_api_route("/logout", self.logout, methods=["POST"], response_model=ApiResponse[None])
        self.router.add_api_route(
            "/validate-email",
            self.validate_email,
            methods=["POST"],
            response_model=ApiResponse[ValidateResponse],
        )
        self.router.add_api_route(
            "/validate-phone",
            self.validate_phone,
            methods=["POST"],
            response_model=ApiResponse[ValidateResponse],
        )

    async def get_me(self, current_user: User = Depends(get_current_user)) -> ApiResponse[UserProfileResponse]:
        data = UserProfileResponse.model_validate(current_user)
        return success_response(data, "User profile fetched successfully")

    @limiter.limit("10/minute")
    async def signup(
        self,
        request: Request,
        payload: SignupRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[UserResponse]:
        data = await container.auth.signup(payload)
        return success_response(data, "Signup successful. Please verify your email OTP.")

    @limiter.limit("10/minute")
    async def verify_email_otp(
        self,
        request: Request,
        payload: VerifyEmailOtpRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[AuthTokenResponse]:
        data = await container.auth.verify_email_otp(payload)
        return success_response(data, "Email verified successfully")

    @limiter.limit("20/minute")
    async def login(
        self,
        request: Request,
        payload: LoginRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[AuthTokenResponse | ChildLoginResponse]:
        data = await container.auth.login(payload)
        message = "Child login successful" if payload.child_login else "Login successful"
        return success_response(data, message)

    @limiter.limit("20/minute")
    async def child_login(
        self,
        request: Request,
        payload: ChildLoginRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildLoginResponse]:
        data = await container.auth.child_login(payload)
        return success_response(data, "Child login successful")

    @limiter.limit("20/minute")
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

    @limiter.limit("5/minute")
    async def forgot_password(
        self,
        request: Request,
        payload: ForgotPasswordRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.auth.forgot_password(payload)
        return success_response(None, "Password reset OTP sent")

    @limiter.limit("5/minute")
    async def reset_password(
        self,
        request: Request,
        payload: ResetPasswordRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.auth.reset_password(payload)
        return success_response(None, "Password reset successfully")

    @limiter.limit("30/minute")
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

    @limiter.limit("20/minute")
    async def validate_email(
        self,
        request: Request,
        payload: ValidateEmailRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ValidateResponse]:
        data = await container.auth.validate_email(payload)
        return success_response(data, "Email validation completed")

    @limiter.limit("20/minute")
    async def validate_phone(
        self,
        request: Request,
        payload: ValidatePhoneRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ValidateResponse]:
        data = await container.auth.validate_phone(payload)
        return success_response(data, "Phone validation completed")


router = AuthRouter(app_container).router
