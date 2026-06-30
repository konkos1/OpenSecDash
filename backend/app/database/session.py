from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings


connect_args = {"check_same_thread": False, "timeout": 10} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
