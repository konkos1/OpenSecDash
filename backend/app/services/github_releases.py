import re

import requests

from app.core.http_responses import read_capped_json

GITHUB_RELEASE_RESPONSE_MAX_BYTES = 256 * 1024


def github_repo_from_url(url: str | None) -> str | None:
    if not url:
        return None

    match = re.search(
        r"github\.com/([^/]+)/([^/]+)/releases/latest/?$",
        url,
    )

    if not match:
        return None

    return f"{match.group(1)}/{match.group(2)}"


def get_latest_github_release(
    repo: str,
    github_token: str | None = None,
) -> str | None:
    headers = {
        "Accept": "application/vnd.github+json",
    }

    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    response = requests.get(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers=headers,
        timeout=15,
        stream=True,
    )

    try:
        response.raise_for_status()
        data = read_capped_json(response, max_bytes=GITHUB_RELEASE_RESPONSE_MAX_BYTES, source="GitHub releases API")
    finally:
        response.close()
    if not isinstance(data, dict):
        return None

    return data.get("tag_name")
