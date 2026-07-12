from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.services.instance_branding import KIND_FAVICON, KIND_LOGO, get_instance_file

router = APIRouter(tags=["instance"])

FILE_HEADERS = {
    "Cache-Control": "public, max-age=86400",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
}


def _instance_file_response(db: Session, kind: str) -> Response:
    row = get_instance_file(db, kind)
    if row is None:
        raise HTTPException(status_code=404)
    return Response(content=row.data, media_type=row.content_type, headers=FILE_HEADERS)


@router.get("/instance/logo")
def instance_logo(db: Session = Depends(get_db)) -> Response:
    return _instance_file_response(db, KIND_LOGO)


@router.get("/instance/favicon")
def instance_favicon(db: Session = Depends(get_db)) -> Response:
    return _instance_file_response(db, KIND_FAVICON)
