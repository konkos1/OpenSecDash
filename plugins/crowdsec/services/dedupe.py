from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import DuplicateRule

# A manual ban/unban action is recorded immediately by the Action framework;
# if CrowdSec's own log later records that same decision, the crowdsec_log
# datasource plugin would otherwise re-import it as a second, separate event
# for the same IP at essentially the same time - visible as an apparent
# duplicate with different (and each incomplete) data.
CROWDSEC_BAN_EVENT_TYPES = ("security.ban", "security.ban.manual")

# The Action framework embeds its own action id into the ban reason it hands
# to CrowdSec (e.g. "Manual ban via OpenSecDash (action #42)", see
# actions.create_action), and CrowdSec echoes that text verbatim into its own
# log line for API/cscli-created decisions - confirmed against a real
# CrowdSec instance:
#   msg="(<machine>/cscli) Manual ban via OpenSecDash (action #42) by ip X : 1m ban on Ip X"
# Matching on this id is exact regardless of timing, unlike a time-window
# heuristic: a ban, followed by an unban, followed by a fresh re-ban of the
# same IP within seconds would otherwise risk merging the re-ban's log line
# into the stale first ban instead of the second one.
CROWDSEC_MANUAL_ACTION_ID_PATTERN = re.compile(r"OpenSecDash.*?\(action\s*#(\d+)\)", re.IGNORECASE)

# Fallback only: used when a crowdsec ban log line can't be correlated by
# action id (defensive - e.g. an unexpected CrowdSec log format change). 10s
# is the default poll interval; 30s comfortably covers that plus manager
# scheduling slack without risking a merge across an unrelated later ban.
CROWDSEC_BAN_DEDUPE_WINDOW = timedelta(seconds=30)


def find_action_duplicate_by_id(db: Session, values: dict[str, Any]) -> Event | None:
    raw_data = values.get("raw_data")
    if values.get("plugin") != "crowdsec" or not raw_data:
        return None
    match = CROWDSEC_MANUAL_ACTION_ID_PATTERN.search(raw_data)
    if not match:
        return None
    action_id = int(match.group(1))
    return (
        db.query(Event)
        .filter(Event.plugin == "crowdsec", func.json_extract(Event.data_json, "$.action_id") == action_id)
        .order_by(Event.id.asc())
        .first()
    )


def find_recent_ban_duplicate(db: Session, values: dict[str, Any]) -> Event | None:
    # Only applies to incoming log-tailed re-imports ("security.ban"), never
    # to a freshly created "security.ban.manual" event: manual bans already
    # carry their own unique action id and must never be time-window-merged
    # into an earlier, unrelated manual ban just because they land close
    # together (e.g. ban -> unban -> re-ban within the fallback window).
    if values.get("plugin") != "crowdsec" or values.get("event_type") != "security.ban":
        return None
    ip = values.get("ip")
    event_time = values.get("event_time")
    if not ip or event_time is None:
        return None
    return (
        db.query(Event)
        .filter(
            Event.plugin == "crowdsec",
            Event.event_type.in_(CROWDSEC_BAN_EVENT_TYPES),
            Event.ip == ip,
            Event.event_time >= event_time - CROWDSEC_BAN_DEDUPE_WINDOW,
            Event.event_time <= event_time + CROWDSEC_BAN_DEDUPE_WINDOW,
        )
        .order_by(Event.id.asc())
        .first()
    )


RULES = (
    DuplicateRule(find=find_action_duplicate_by_id, backfill_keys=("scenario", "duration")),
    DuplicateRule(find=find_recent_ban_duplicate, backfill_keys=("scenario", "duration")),
)
