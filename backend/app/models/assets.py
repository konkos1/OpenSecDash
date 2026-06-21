from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from datetime import datetime

from app.database.base import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)

    system_id: Mapped[int] = mapped_column(
        ForeignKey("systems.id")
    )

    system = relationship("System")

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    name: Mapped[str]

    version: Mapped[str]

    latest_version: Mapped[str | None]

    release_url: Mapped[str | None]

    update_available: Mapped[bool] = mapped_column(
        Boolean,
        default=False
    )
