import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

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

    async def send_story_completed_email(
        self,
        email: str,
        *,
        story_title: str,
        story_summary: str | None,
        story_input: dict[str, Any] | None,
    ) -> None:
        subject = f"Your story is ready: {story_title}"
        input_lines = self._story_input_plain_lines(story_input)
        plain_body = (
            f"Your story \"{story_title}\" is generated and ready to read.\n\n"
            f"Summary:\n{story_summary or 'Your new personalized story is ready in the dashboard.'}\n\n"
            "Story input used:\n"
            f"{chr(10).join(input_lines) if input_lines else 'No story input was provided.'}\n\n"
            "Open the dashboard to read the story now."
        )
        html_body = self._build_story_completed_template(
            story_title=story_title,
            story_summary=story_summary,
            story_input=story_input,
        )
        await self._send(email, subject, plain_body, html_body)
        logger.info("story_completed_email_sent", email=email, story_title=story_title)

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

    def _build_story_completed_template(
        self,
        *,
        story_title: str,
        story_summary: str | None,
        story_input: dict[str, Any] | None,
    ) -> str:
        safe_title = html.escape(story_title)
        safe_summary = html.escape(story_summary or "Your new personalized story is ready in the dashboard.")
        input_rows = "".join(
            f"""
                <tr>
                  <th>{html.escape(label)}</th>
                  <td>{html.escape(value)}</td>
                </tr>
            """
            for label, value in self._story_input_display_rows(story_input)
        )
        if not input_rows:
            input_rows = """
                <tr>
                  <td colspan="2">No story input was provided.</td>
                </tr>
            """

        return self._layout(
            eyebrow="Story ready",
            title="Your story is ready to read",
            intro=f'"{story_title}" has finished generating and is waiting in your dashboard.',
            content=f"""
                <div class="panel">
                    <strong>Story title</strong>
                    <p>{safe_title}</p>
                </div>
                <div class="panel panel-soft">
                    <strong>Summary</strong>
                    <p>{safe_summary}</p>
                </div>
                <div class="input-block">
                    <strong>Story input used</strong>
                    <table role="presentation" cellspacing="0" cellpadding="0">
                        {input_rows}
                    </table>
                </div>
                <p class="cta-text">Open your dashboard to read the story now.</p>
            """,
            footer="This notification was sent because story generation completed successfully.",
        )

    @staticmethod
    def _story_input_display_rows(story_input: dict[str, Any] | None) -> list[tuple[str, str]]:
        if not isinstance(story_input, dict):
            return []
        fields = [
            ("Mode", story_input.get("mode")),
            ("Processing mode", story_input.get("processing_mode")),
            ("Category", story_input.get("category")),
            ("Learning goal", story_input.get("learning_goal")),
            ("Context", story_input.get("context")),
            ("Event", story_input.get("event_description")),
        ]
        return [
            (label, str(value).strip())
            for label, value in fields
            if value is not None and str(value).strip()
        ]

    @classmethod
    def _story_input_plain_lines(cls, story_input: dict[str, Any] | None) -> list[str]:
        return [f"- {label}: {value}" for label, value in cls._story_input_display_rows(story_input)]

    @staticmethod
    def _brand_name() -> str:
        app_name = (settings.APP_NAME or "").strip()
        return "TaleSpell" if not app_name or app_name == "SB Backend" else app_name

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
    .panel-soft {{
      background: #fff8f2;
      border-left-color: #c45b3c;
    }}
    .panel p {{
      margin: 8px 0 0;
    }}
    .input-block {{
      margin-top: 22px;
    }}
    table {{
      border-collapse: collapse;
      margin-top: 12px;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid #edf0f2;
      font-size: 14px;
      line-height: 1.5;
      padding: 10px 0;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: #425466;
      font-weight: 700;
      width: 36%;
      padding-right: 16px;
    }}
    .cta-text {{
      color: #28536b;
      font-weight: 700;
      margin-top: 24px;
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
      <div class="header"><div class="brand">{html.escape(self._brand_name())}</div></div>
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
