from pathlib import Path
import re


INLINE_EVENT_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def _browser_html_files() -> list[Path]:
    roots = [
        Path("app/templates"),
        Path("app/static"),
        Path("../plugins"),
    ]
    return sorted(path for root in roots for path in root.rglob("*.html"))


def test_browser_html_has_no_csp_blocked_inline_event_handlers():
    offenders = []
    for path in _browser_html_files():
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if INLINE_EVENT_HANDLER.search(line):
                offenders.append(f"{path}:{line_number}")

    assert offenders == []


def test_csp_safe_interactions_keep_external_script_and_html_fallback_hooks():
    script = Path("app/static/js/app.js").read_text()
    rollups = Path("app/templates/rollups.html").read_text()
    assets = Path("app/templates/assets.html").read_text()
    offline = Path("app/static/offline.html").read_text()

    assert "form[data-rollups-selector]" in script
    assert "form.requestSubmit()" in script
    assert "data-rollups-selector" in rollups
    assert 'form[data-assets-filters]' in script
    assert 'checkbox.addEventListener("change", () => form.requestSubmit())' in script
    assert "data-assets-filters" in assets
    assert "data-clickable-row" in assets
    assert 'event.target.closest("[data-clickable-row]")' in script
    assert '<a class="retry-link" href="/">' in offline
