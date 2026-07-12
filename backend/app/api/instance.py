import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.core.template_context import get_setting_value
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


@router.get("/manifest.webmanifest")
def instance_manifest(db: Session = Depends(get_db)) -> JSONResponse:
    domain = get_setting_value(db, "domain", "")
    favicon = get_instance_file(db, KIND_FAVICON)
    icons = [
        {"src": "/static/img/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/img/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/img/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "/static/img/opensecdash-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ]
    if favicon is not None and favicon.content_type in {"image/png", "image/webp", "image/svg+xml"}:
        icons.insert(0, {"src": f"/instance/favicon?v={favicon.updated_at}", "sizes": "any", "type": favicon.content_type, "purpose": "any"})
    return JSONResponse({"name": f"OpenSecDash · {domain}" if domain else "OpenSecDash", "short_name": "OpenSecDash", "description": get_setting_value(db, "instance_description", "") or "A security dashboard for homelabs.", "start_url": "/", "scope": "/", "display": "standalone", "background_color": "#0f172a", "theme_color": "#0f172a", "icons": icons}, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


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
