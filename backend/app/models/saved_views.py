from datetime import datetime

from sqlalchemy import DateTime, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.database.base import Base


class SavedView(Base):
    __tablename__ = "saved_views"
    __table_args__ = (UniqueConstraint("scope", "name", name="uq_saved_view_scope_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(20), index=True)
    filter_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
