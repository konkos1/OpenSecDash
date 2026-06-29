from datetime import UTC, datetime

from app.core.time import datetime_iso_utc, format_datetime_for_timezone, resolve_timezone
from app.locales.de import DE
from app.locales.en import EN


def test_locales_expose_the_same_translation_keys():
    assert set(DE) == set(EN)


def test_timezone_formatting_uses_configured_zone_and_falls_back_to_utc():
    value = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    assert format_datetime_for_timezone(value, "Europe/Berlin") == "2026-06-01 14:00:00"
    assert format_datetime_for_timezone(value, "not/a-zone") == "2026-06-01 12:00:00"
    assert resolve_timezone("auto").key == "UTC"
    assert datetime_iso_utc(datetime(2026, 6, 1, 14, 0, 0, tzinfo=resolve_timezone("Europe/Berlin"))) == "2026-06-01T12:00:00+00:00"
