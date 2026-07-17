import pytest
from starlette.requests import Request

from app.web.tables import column_redirect_url


def _request(referer: str | None = None) -> Request:
    headers = [(b"host", b"opensecdash.local")]
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("opensecdash.local", 443),
            "method": "POST",
            "path": "/events/columns",
            "headers": headers,
            "query_string": b"",
        }
    )


def test_column_redirect_rejects_external_referer():
    assert column_redirect_url(_request("https://evil.example/events"), "/events", "") == "/events"


@pytest.mark.parametrize(
    "referer",
    (
        "//evil.example/events",
        r"/\evil.example/events",
        r"/\\evil.example/events",
        r"\evil.example/events",
        r"https:\evil.example/events",
    ),
)
def test_column_redirect_rejects_ambiguous_referer(referer: str):
    assert column_redirect_url(_request(referer), "/events", "") == "/events"


def test_column_redirect_allows_same_origin_referer_as_local_path():
    result = column_redirect_url(_request("https://opensecdash.local/events?today=true#table"), "/events", "")

    assert result == "/events?today=true#table"


def test_column_redirect_adds_snapshot_to_safe_local_path():
    result = column_redirect_url(_request("/access?q=wp-login"), "/access", "2026-07-09T12:00:00")

    assert result == "/access?q=wp-login&snapshot_before=2026-07-09T12%3A00%3A00"
