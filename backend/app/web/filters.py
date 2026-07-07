from datetime import datetime
from http import HTTPStatus
from urllib.parse import quote

from jinja2 import Environment, pass_context
from markupsafe import Markup, escape

from app.core.time import datetime_iso_utc, format_datetime_for_timezone
from app.models.events import Event
from app.services.events import is_local_ip_value

# Jinja filters and template globals shared by core pages and plugin pages.
# Kept free of the FastAPI/templates instance so app.web.templates can import
# and register these without a circular import.


@pass_context
def format_duration(context, value: str | None) -> str:
    if not value:
        return "-"
    match = __import__("re").match(r"^(\d+)([smhdw])$", str(value).strip().lower())
    if not match:
        return str(value)
    amount = int(match.group(1))
    unit = match.group(2)
    language = str(context.get("language", "en"))
    labels = {
        "de": {"s": ("Sekunde", "Sekunden"), "m": ("Minute", "Minuten"), "h": ("Stunde", "Stunden"), "d": ("Tag", "Tage"), "w": ("Woche", "Wochen")},
        "en": {"s": ("second", "seconds"), "m": ("minute", "minutes"), "h": ("hour", "hours"), "d": ("day", "days"), "w": ("week", "weeks")},
    }
    singular, plural = labels.get(language, labels["en"])[unit]
    return f"{amount} {singular if amount == 1 else plural}"


@pass_context
def format_country_name(context, value: str | None) -> Markup | str:
    if not value:
        return "-"
    code = str(value).upper()
    return Markup('<span class="osd-country" data-country-code="{}">{}</span>'.format(escape(code), escape(code)))


@pass_context
def format_country_or_local(context, value: str | None, ip: str | None = None) -> Markup | str:
    if is_local_ip_value(ip):
        translator = context.get("t")
        return str(translator("common.local")) if callable(translator) else "local"
    return format_country_name(context, value)


@pass_context
def format_datetime(context, value: datetime | None) -> Markup | str:
    if value is None:
        return "-"
    timezone = str(context.get("timezone", "auto"))
    text = format_datetime_for_timezone(value, timezone)
    iso_utc = datetime_iso_utc(value)
    return Markup(
        '<span class="osd-datetime" data-datetime-utc="{}" data-timezone="{}">{}</span>'.format(
            escape(iso_utc),
            escape(timezone),
            escape(text),
        )
    )


def url_path_quote(value: str | None) -> str:
    return quote(str(value or ""), safe="")


def http_status_label(value: int | None) -> str:
    if value is None:
        return ""
    try:
        status = HTTPStatus(int(value))
        return f"{status.value} {status.phrase}"
    except ValueError:
        return str(value)


def event_url(event: Event) -> str:
    path = event.path or ""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path

    data = event.data_json or {}
    for key in ("url", "full_url", "request_url", "absolute_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value

    host = event.hostname or data.get("host") or data.get("request_host")
    if not host:
        return path

    scheme = data.get("scheme") or data.get("request_scheme") or data.get("RequestScheme") or data.get("proto") or data.get("protocol")
    if not scheme:
        router = str(data.get("router_name") or "").lower()
        if "https" in router or "websecure" in router:
            scheme = "https"
        elif "http" in router or "web" in router:
            scheme = "http"
    if not scheme:
        return f"{host}{path if path.startswith('/') else '/' + path}"

    scheme = str(scheme).replace("://", "").lower()
    if scheme not in {"http", "https"}:
        scheme = "https" if scheme.startswith("https") else "http"
    display_path = path if path.startswith("/") else f"/{path}"
    return f"{scheme}://{host}{display_path}"


def event_data_value(event: Event, *keys: str) -> str | None:
    data = event.data_json or {}
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def register_filters(env: Environment) -> None:
    env.filters["datetime"] = format_datetime
    env.filters["duration"] = format_duration
    env.filters["country_name"] = format_country_name
    env.filters["country_or_local"] = format_country_or_local
    env.filters["url_path_quote"] = url_path_quote
    env.filters["event_url"] = event_url
    env.filters["http_status_label"] = http_status_label
    # Was injected into every render() context; a global keeps templates
    # working without render() needing to import from pages.
    env.globals["event_data_value"] = event_data_value
