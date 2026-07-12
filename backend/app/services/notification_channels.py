import smtplib
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value


class NotificationChannel(Protocol):
    id: str

    def is_configured(self, db: Session) -> bool: ...

    def send(self, db: Session, subject: str, body: str) -> None: ...


class EmailChannel:
    id = "email"

    def is_configured(self, db: Session) -> bool:
        return all(
            get_setting_value(db, key, "").strip()
            for key in ("notifications.smtp_host", "notifications.smtp_sender", "notifications.smtp_recipient")
        )

    def send(self, db: Session, subject: str, body: str) -> None:
        host = get_setting_value(db, "notifications.smtp_host", "")
        port_text = get_setting_value(db, "notifications.smtp_port", "587")
        try:
            port = int(port_text)
        except ValueError:
            port = 587
        security = get_setting_value(db, "notifications.smtp_security", "starttls")
        user = get_setting_value(db, "notifications.smtp_user", "")
        password = get_setting_value(db, "notifications.smtp_password", "")
        sender = get_setting_value(db, "notifications.smtp_sender", "")
        recipient = get_setting_value(db, "notifications.smtp_recipient", "")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)
        smtp_class = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
        with smtp_class(host, port, timeout=10) as smtp:
            if security == "starttls":
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)


_CHANNELS: dict[str, NotificationChannel] = {"email": EmailChannel()}


def get_channel(channel_id: str) -> NotificationChannel | None:
    return _CHANNELS.get(channel_id)
