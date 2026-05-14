import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class EmailClient:
    """SMTP email integration boundary.

    The OTP value is intentionally not logged.
    """

    async def send_otp_email(self, email: str, otp: str) -> None:
        subject = "Verify your Storybook email"
        plain_body = (
            "Welcome to Storybook.\n\n"
            f"Your email verification code is {otp}.\n"
            f"This code expires in {settings.OTP_EXPIRE_MINUTES} minutes.\n\n"
            "If you did not request this code, you can safely ignore this email."
        )
        html_body = self._build_otp_template(otp)
        await self._send(email, subject, plain_body, html_body)
        logger.info("otp_email_sent", email=email)

    async def send_welcome_email(self, email: str) -> None:
        subject = "Welcome to Storybook"
        plain_body = (
            "Your Storybook email is verified.\n\n"
            "You can now sign in and start creating personalized storytelling moments."
        )
        html_body = self._build_welcome_template()
        await self._send(email, subject, plain_body, html_body)
        logger.info("welcome_email_sent", email=email)

    async def _send(self, recipient: str, subject: str, plain_body: str, html_body: str) -> None:
        await asyncio.to_thread(self._send_sync, recipient, subject, plain_body, html_body)

    def _send_sync(self, recipient: str, subject: str, plain_body: str, html_body: str) -> None:
        self._ensure_configured()
        from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((settings.SMTP_FROM_NAME, from_email))
        message["To"] = recipient
        message.set_content(plain_body)
        message.add_alternative(html_body, subtype="html")

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.ehlo()
                if settings.SMTP_USE_TLS:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            logger.exception("email_send_failed", email=recipient, subject=subject)
            raise

    def _ensure_configured(self) -> None:
        missing = [
            name
            for name, value in {
                "SMTP_HOST": settings.SMTP_HOST,
                "SMTP_PORT": settings.SMTP_PORT,
                "SMTP_USERNAME": settings.SMTP_USERNAME,
                "SMTP_PASSWORD": settings.SMTP_PASSWORD,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Email SMTP configuration is missing: {', '.join(missing)}")

    def _build_otp_template(self, otp: str) -> str:
        safe_otp = html.escape(otp)
        return self._layout(
            eyebrow="Email verification",
            title="Confirm your email address",
            intro="Use the verification code below to finish setting up your Storybook account.",
            content=f"""
                <div class="code">{safe_otp}</div>
                <p class="meta">This code expires in {settings.OTP_EXPIRE_MINUTES} minutes.</p>
            """,
            footer="If you did not request this code, no action is needed.",
        )

    def _build_welcome_template(self) -> str:
        return self._layout(
            eyebrow="You're all set",
            title="Welcome to Storybook",
            intro="Your email is verified. You can now sign in and create personalized storytelling moments for your family.",
            content="""
                <div class="panel">
                    <strong>What happens next?</strong>
                    <p>Complete your child profile, choose story preferences, and start building stories made for your home.</p>
                </div>
            """,
            footer="Thank you for joining Storybook.",
        )

    def _layout(self, eyebrow: str, title: str, intro: str, content: str, footer: str) -> str:
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      margin: 0;
      background: #f6f2ea;
      color: #1f2933;
      font-family: Arial, Helvetica, sans-serif;
    }}
    .wrap {{
      width: 100%;
      padding: 32px 16px;
    }}
    .email {{
      max-width: 560px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #e7dfd1;
      border-radius: 8px;
      overflow: hidden;
    }}
    .header {{
      background: #28536b;
      color: #ffffff;
      padding: 26px 32px;
    }}
    .brand {{
      font-size: 18px;
      font-weight: 700;
    }}
    .body {{
      padding: 32px;
    }}
    .eyebrow {{
      color: #c45b3c;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin: 0 0 10px;
    }}
    h1 {{
      font-size: 26px;
      line-height: 1.25;
      margin: 0 0 14px;
    }}
    p {{
      font-size: 16px;
      line-height: 1.6;
      margin: 0 0 18px;
    }}
    .code {{
      background: #f4f7f8;
      border: 1px solid #d8e4e8;
      border-radius: 8px;
      color: #193847;
      font-size: 34px;
      font-weight: 700;
      letter-spacing: 0.16em;
      margin: 24px 0 12px;
      padding: 18px 20px;
      text-align: center;
    }}
    .meta {{
      color: #64748b;
      font-size: 14px;
    }}
    .panel {{
      background: #f8faf5;
      border-left: 4px solid #6c9a8b;
      border-radius: 6px;
      margin-top: 22px;
      padding: 18px 20px;
    }}
    .panel p {{
      margin: 8px 0 0;
    }}
    .footer {{
      border-top: 1px solid #edf0f2;
      color: #64748b;
      font-size: 13px;
      padding: 20px 32px 28px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="email">
      <div class="header"><div class="brand">Storybook</div></div>
      <div class="body">
        <p class="eyebrow">{html.escape(eyebrow)}</p>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(intro)}</p>
        {content}
      </div>
      <div class="footer">{html.escape(footer)}</div>
    </div>
  </div>
</body>
</html>"""


email_client = EmailClient()
