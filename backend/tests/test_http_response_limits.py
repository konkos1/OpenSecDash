import json

import pytest

from app.core.http_responses import ResponseBodyError, read_capped_json
from app.services import github_releases
from app.services.geoip import service as geoip_service
from app.services.geoip.providers import ip_api


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.body = body
        self.headers = headers or {}
        self.closed = False
        self.chunks_read = 0

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            self.chunks_read += 1
            yield self.body[index : index + chunk_size]

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True


def test_capped_json_rejects_declared_and_streamed_oversize():
    declared = FakeResponse(b"{}", {"Content-Length": "17"})
    with pytest.raises(ResponseBodyError, match="too large"):
        read_capped_json(declared, max_bytes=16, source="test API")
    assert declared.chunks_read == 0

    streamed = FakeResponse(b'{"value":"' + b"x" * 32 + b'"}')
    with pytest.raises(ResponseBodyError, match="too large"):
        read_capped_json(streamed, max_bytes=16, source="test API")
    assert streamed.chunks_read == 1


def test_geoip_reads_streamed_response_with_cap(monkeypatch, db_session):
    response = FakeResponse(json.dumps({"status": "success", "countryCode": "DE"}).encode())
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(ip_api.requests, "get", fake_get)

    assert geoip_service._lookup_provider_geoip(db_session, "ip-api", "8.8.8.8") == ("DE", None, None, None)
    assert calls[0][1]["stream"] is True
    assert response.closed is True


def test_github_release_reads_streamed_response_with_cap(monkeypatch):
    response = FakeResponse(json.dumps({"tag_name": "v1.2.3"}).encode())
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(github_releases.requests, "get", fake_get)

    assert github_releases.get_latest_github_release("owner/repo") == "v1.2.3"
    assert calls[0][1]["stream"] is True
    assert response.closed is True


def test_github_release_rejects_oversized_response(monkeypatch):
    response = FakeResponse(b"", {"Content-Length": str(github_releases.GITHUB_RELEASE_RESPONSE_MAX_BYTES + 1)})
    monkeypatch.setattr(github_releases.requests, "get", lambda *args, **kwargs: response)

    with pytest.raises(ResponseBodyError, match="too large"):
        github_releases.get_latest_github_release("owner/repo")
    assert response.closed is True
