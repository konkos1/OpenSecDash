from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class System(Base):
    __tablename__ = "systems"

    id: Mapped[int] = mapped_column(primary_key=True)
    vmid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hostname: Mapped[str] = mapped_column(String(255), index=True)
    system_type: Mapped[str] = mapped_column(String(100), default="custom")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assets = relationship("Asset", back_populates="system")
