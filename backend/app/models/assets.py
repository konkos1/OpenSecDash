from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[int] = mapped_column(ForeignKey("systems.id"), index=True)
    system = relationship("System", back_populates="assets")

    name: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(String(50), default="application")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    version: Mapped[str] = mapped_column(String(255), default="")
    latest_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    release_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    release_api_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    release_web_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    update_check_type: Mapped[str] = mapped_column(String(50), default="github_release")
    update_available: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
