import pytest
from starlette.requests import Request

from app.web.redirects import is_safe_local_path, safe_local_path, safe_local_redirect_target


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("opensecdash.local", 443),
            "method": "POST",
            "path": "/login",
            "headers": [(b"host", b"opensecdash.local")],
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    "value",
    (
        "https://evil.example",
        "//evil.example",
        r"/\evil.example",
        r"/\\evil.example",
        r"\evil.example",
        r"https:\evil.example",
    ),
)
def test_local_path_helpers_reject_external_and_ambiguous_urls(value: str):
    assert not is_safe_local_path(value)
    assert safe_local_path(value, "/fallback") == "/fallback"


def test_local_path_helpers_allow_paths_with_queries_and_fragments():
    target = "/events?today=true#table"

    assert is_safe_local_path(target)
    assert safe_local_path(f"  {target}  ") == target


def test_local_redirect_target_converts_same_origin_url_to_local_path():
    target = safe_local_redirect_target(
        _request(),
        "https://opensecdash.local/events?today=true#table",
        "/fallback",
    )

    assert target == "/events?today=true#table"


@pytest.mark.parametrize(
    "target",
    (
        "https://evil.example/events",
        r"/\evil.example/events",
        r"https://opensecdash.local/\evil.example",
        "https://[",
    ),
)
def test_local_redirect_target_uses_fallback_for_unsafe_urls(target: str):
    assert safe_local_redirect_target(_request(), target, "/fallback") == "/fallback"


def test_redirect_helpers_do_not_return_an_unsafe_fallback():
    assert safe_local_path(None, r"/\evil.example") == "/"
    assert safe_local_redirect_target(_request(), None, r"/\evil.example") == "/"
