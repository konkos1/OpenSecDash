import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value


EMAIL_LOGO_CONTENT_ID = "opensecdash-logo"
EMAIL_LOGO_PATH = Path(__file__).resolve().parents[1] / "static" / "img" / "pwa" / "icon-192.png"
_email_templates = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates" / "email"),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_email_html(
    *,
    subject: str,
    heading: str,
    language: str,
    body: str | None = None,
    details: list[tuple[str, str]] | None = None,
    items: list[str] | None = None,
    more_text: str | None = None,
    links: list[tuple[str, str]] | None = None,
) -> str:
    """Render the shared branded email body with automatic HTML escaping."""
    return _email_templates.get_template("notification.html").render(
        subject=subject,
        heading=heading,
        language=language,
        body=body,
        details=details or [],
        items=items or [],
        more_text=more_text,
        links=links or [],
    )


class NotificationChannel(Protocol):
    id: str

    def is_configured(self, db: Session) -> bool: ...

    def send(self, db: Session, subject: str, body: str, html_body: str | None = None) -> None: ...


class EmailChannel:
    id = "email"

    def _tls_context(self, db: Session) -> ssl.SSLContext:
        context = ssl.create_default_context()
        ca_file = get_setting_value(db, "notifications.smtp_ca_file", "").strip()
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        return context

    def is_configured(self, db: Session) -> bool:
        return all(
            get_setting_value(db, key, "").strip()
            for key in ("notifications.smtp_host", "notifications.smtp_sender", "notifications.smtp_recipient")
        )

    def send(self, db: Session, subject: str, body: str, html_body: str | None = None) -> None:
        host = get_setting_value(db, "notifications.smtp_host", "")
        port_text = get_setting_value(db, "notifications.smtp_port", "587")
        try:
            port = int(port_text)
        except ValueError:
            port = 587
        security = get_setting_value(db, "notifications.smtp_security", "starttls")
        if security not in {"none", "starttls", "ssl"}:
            raise ValueError("Unsupported SMTP security mode")
        user = get_setting_value(db, "notifications.smtp_user", "")
        password = get_setting_value(db, "notifications.smtp_password", "")
        sender = get_setting_value(db, "notifications.smtp_sender", "")
        recipient = get_setting_value(db, "notifications.smtp_recipient", "")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)
        rendered_html = html_body or render_email_html(
            subject=subject,
            heading=subject,
            language=get_setting_value(db, "language", "en"),
            body=body,
        )
        message.add_alternative(rendered_html, subtype="html")
        html_part = message.get_body(preferencelist=("html",))
        if html_part is not None:
            html_part.add_related(
                EMAIL_LOGO_PATH.read_bytes(),
                maintype="image",
                subtype="png",
                cid=f"<{EMAIL_LOGO_CONTENT_ID}>",
                disposition="inline",
                filename="opensecdash.png",
            )
        tls_context = self._tls_context(db) if security in {"starttls", "ssl"} else None
        if security == "ssl":
            smtp_connection = smtplib.SMTP_SSL(host, port, timeout=10, context=tls_context)
        else:
            smtp_connection = smtplib.SMTP(host, port, timeout=10)
        with smtp_connection as smtp:
            if security == "starttls":
                assert tls_context is not None
                smtp.starttls(context=tls_context)
            if user:
                smtp.login(user, password)
            smtp.send_message(message)


_CHANNELS: dict[str, NotificationChannel] = {"email": EmailChannel()}


def get_channel(channel_id: str) -> NotificationChannel | None:
    return _CHANNELS.get(channel_id)
