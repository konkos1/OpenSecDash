from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import JSON
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)

    timestamp: Mapped[datetime]

    source: Mapped[str] = mapped_column(String(100))

    plugin: Mapped[str] = mapped_column(String(100))

    event_type: Mapped[str] = mapped_column(String(50))

    ip: Mapped[str | None] = mapped_column(
        String(64),
        index=True
    )

    country: Mapped[str | None] = mapped_column(
        String(8)
    )

    hostname: Mapped[str | None] = mapped_column(
        String(255)
    )

    status_code: Mapped[int | None]

    path: Mapped[str | None]

    severity: Mapped[str]

    data_json: Mapped[dict | None] = mapped_column(
        JSON
    )
