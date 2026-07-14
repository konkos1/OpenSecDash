import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.core.template_context import get_setting_value
from app.services.instance_branding import (
    KIND_FAVICON,
    KIND_LOGO,
    MAX_BYTES,
    delete_instance_file,
    get_instance_file,
    pwa_icon_size,
    save_instance_file,
    validate_upload,
)
from app.web.tables import clean_url_value, save_setting

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
    ]
    if favicon is not None and favicon.content_type in {"image/png", "image/webp", "image/svg+xml"}:
        custom_size = pwa_icon_size(favicon.data, favicon.content_type)
        if custom_size is not None:
            icons.insert(0, {"src": f"/instance/favicon?v={favicon.updated_at}", "sizes": custom_size, "type": favicon.content_type, "purpose": "any"})
    return JSONResponse({"id": "/", "name": f"OpenSecDash · {domain}" if domain else "OpenSecDash", "short_name": "OpenSecDash", "description": get_setting_value(db, "instance_description", "") or "A security dashboard for homelabs.", "start_url": "/", "scope": "/", "display": "standalone", "background_color": "#0f172a", "theme_color": "#0f172a", "icons": icons}, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.post("/settings/branding")
async def save_instance_branding(
    domain: str | None = Form(None),
    instance_description: str | None = Form(None),
    logo: UploadFile | None = File(None),
    favicon: UploadFile | None = File(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    uploads = []
    for kind, upload in ((KIND_LOGO, logo), (KIND_FAVICON, favicon)):
        if upload is None:
            continue
        data = await upload.read(MAX_BYTES[kind] + 1)
        if upload.filename and data:
            uploads.append((kind, upload.filename, data))

    def _save() -> None:
        for kind, _filename, data in uploads:
            validate_upload(kind, data)
        for kind, filename, data in uploads:
            save_instance_file(db, kind, filename, data, commit=False)
        if domain is not None:
            save_setting(db, "domain", clean_url_value(domain))
        if instance_description is not None:
            save_setting(db, "instance_description", instance_description.strip()[:500])
        db.commit()

    try:
        await asyncio.to_thread(_save)
    except ValueError as exc:
        await asyncio.to_thread(db.rollback)
        return RedirectResponse(url=f"/settings?branding_error={exc}", status_code=303)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/branding/remove")
async def remove_instance_branding(kind: str = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    if kind in {KIND_LOGO, KIND_FAVICON}:
        def _delete() -> None:
            delete_instance_file(db, kind, commit=False)
            db.commit()

        await asyncio.to_thread(_delete)
    return RedirectResponse(url="/settings", status_code=303)
