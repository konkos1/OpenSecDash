import json
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from app.core.input_limits import MAX_ASSET_INVENTORY_BYTES
from app.core.remote_urls import RemoteURLPolicyError, validate_remote_url


CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 3
READ_CHUNK_BYTES = 64 * 1024


class AssetSourceError(ValueError):
    """Stable source error safe for settings, diagnostics, and API responses."""


def _response_bytes(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length", "")
    try:
        declared_length = int(content_length) if content_length else None
    except ValueError as exc:
        raise AssetSourceError("assets.json returned an invalid Content-Length") from exc
    if declared_length is not None and declared_length > MAX_ASSET_INVENTORY_BYTES:
        raise AssetSourceError("assets.json response exceeds the 10 MiB limit")

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and not (
        content_type in {"application/json", "application/octet-stream", "text/json", "text/plain"}
        or content_type.endswith("+json")
    ):
        raise AssetSourceError("assets.json response has an unsupported Content-Type")

    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding in {"", "identity"}:
        decompressor = None
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj()
    else:
        raise AssetSourceError("assets.json response uses an unsupported Content-Encoding")

    compressed_size = 0
    decoded_size = 0
    chunks: list[bytes] = []
    while True:
        chunk = response.raw.read(READ_CHUNK_BYTES, decode_content=False)
        if not chunk:
            break
        compressed_size += len(chunk)
        if compressed_size > MAX_ASSET_INVENTORY_BYTES:
            raise AssetSourceError("assets.json compressed response exceeds the 10 MiB limit")
        try:
            decoded = (
                decompressor.decompress(chunk, MAX_ASSET_INVENTORY_BYTES - decoded_size + 1)
                if decompressor is not None
                else chunk
            )
        except zlib.error as exc:
            raise AssetSourceError("assets.json response compression is invalid") from exc
        decoded_size += len(decoded)
        if decoded_size > MAX_ASSET_INVENTORY_BYTES:
            raise AssetSourceError("assets.json response exceeds the 10 MiB unpacked limit")
        if decompressor is not None and decompressor.unconsumed_tail:
            raise AssetSourceError("assets.json response exceeds the 10 MiB unpacked limit")
        chunks.append(decoded)
    if decompressor is not None:
        try:
            final = decompressor.flush(MAX_ASSET_INVENTORY_BYTES - decoded_size + 1)
        except zlib.error as exc:
            raise AssetSourceError("assets.json response compression is invalid") from exc
        decoded_size += len(final)
        if decoded_size > MAX_ASSET_INVENTORY_BYTES:
            raise AssetSourceError("assets.json response exceeds the 10 MiB unpacked limit")
        chunks.append(final)
    return b"".join(chunks)


def _load_url(source: str) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    current = source
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                current = validate_remote_url(current)
                response = session.get(
                    current,
                    headers={"Accept": "application/json, application/octet-stream", "Accept-Encoding": "gzip, deflate, identity"},
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                    allow_redirects=False,
                    stream=True,
                )
            except (RemoteURLPolicyError, requests.RequestException) as exc:
                raise AssetSourceError("assets.json URL could not be fetched safely") from exc
            try:
                if 300 <= response.status_code < 400:
                    if redirect_count >= MAX_REDIRECTS:
                        raise AssetSourceError("assets.json redirect limit exceeded")
                    location = response.headers.get("Location", "")
                    if not location:
                        raise AssetSourceError("assets.json redirect has no target")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise AssetSourceError(f"assets.json request failed with HTTP {response.status_code}")
                raw = _response_bytes(response)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AssetSourceError("assets.json response is not valid UTF-8 JSON") from exc
                if not isinstance(payload, dict):
                    raise AssetSourceError("assets.json root must be an object")
                return payload
            finally:
                response.close()
    finally:
        session.close()
    raise AssetSourceError("assets.json redirect limit exceeded")


def load_asset_source(
    source_type: str,
    source: str,
) -> dict[str, Any]:
    if source_type == "url":
        return _load_url(source)

    if source_type == "file":
        path = Path(source)
        if path.stat().st_size > MAX_ASSET_INVENTORY_BYTES:
            raise AssetSourceError("assets.json file exceeds the 10 MiB limit")
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssetSourceError("assets.json file is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise AssetSourceError("assets.json root must be an object")
        return payload

    raise AssetSourceError(
        f"Unsupported asset source type: {source_type}"
    )
