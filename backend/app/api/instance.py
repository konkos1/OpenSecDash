import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.services.instance_branding import (
    KIND_FAVICON,
    KIND_LOGO,
    delete_instance_file,
    get_instance_file,
    save_instance_file,
)

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


@router.post("/settings/branding")
async def save_instance_branding(
    logo: UploadFile | None = File(None),
    favicon: UploadFile | None = File(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    uploads = []
    for kind, upload in ((KIND_LOGO, logo), (KIND_FAVICON, favicon)):
        if upload is None:
            continue
        data = await upload.read()
        if upload.filename and data:
            uploads.append((kind, upload.filename, data))

    def _save() -> None:
        for kind, filename, data in uploads:
            save_instance_file(db, kind, filename, data)

    try:
        await asyncio.to_thread(_save)
    except ValueError as exc:
        return RedirectResponse(url=f"/settings?branding_error={exc}", status_code=303)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/branding/remove")
async def remove_instance_branding(kind: str = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    if kind in {KIND_LOGO, KIND_FAVICON}:
        await asyncio.to_thread(delete_instance_file, db, kind)
    return RedirectResponse(url="/settings", status_code=303)
