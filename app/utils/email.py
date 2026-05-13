from app.core.logger import get_logger

logger = get_logger(__name__)


class EmailClient:
    """Email integration boundary.

    Replace this implementation with SES, SendGrid, Mailgun, or another provider.
    The OTP value is intentionally not logged.
    """

    async def send_otp_email(self, email: str, otp: str) -> None:
        logger.info("otp_email_queued", email=email)

    async def send_welcome_email(self, email: str) -> None:
        logger.info("welcome_email_queued", email=email)


email_client = EmailClient()
