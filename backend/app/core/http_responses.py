"""Bounded readers for HTTP responses from remote integrations."""

import json
from collections.abc import Iterator, Mapping
from typing import Any, Protocol


class StreamingResponse(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...


READ_CHUNK_BYTES = 64 * 1024


class ResponseBodyError(ValueError):
    """Stable error for invalid or oversized remote response bodies."""


def read_capped_json(response: StreamingResponse, *, max_bytes: int, source: str) -> Any:
    """Decode a streamed JSON response without buffering beyond ``max_bytes``."""
    declared = response.headers.get("Content-Length", "")
    if declared:
        try:
            declared_length = int(declared)
        except ValueError as exc:
            raise ResponseBodyError(f"{source} returned an invalid Content-Length") from exc
        if declared_length < 0:
            raise ResponseBodyError(f"{source} returned an invalid Content-Length")
        if declared_length > max_bytes:
            raise ResponseBodyError(f"{source} response is too large")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=READ_CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ResponseBodyError(f"{source} response is too large")
        chunks.append(chunk)

    try:
        return json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResponseBodyError(f"{source} returned invalid JSON") from exc
