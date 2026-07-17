from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class System(Base):
    __tablename__ = "systems"
    __table_args__ = (UniqueConstraint("source_plugin", "external_id", name="uq_system_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vmid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(255), index=True)
    system_type: Mapped[str] = mapped_column(String(100), default="custom")
    source_plugin: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    offline_event_for_last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assets = relationship("Asset", back_populates="system")
