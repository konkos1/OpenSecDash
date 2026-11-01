# ADR-031: Export Framework

> **Implementation status (2026-11-01):** Partially implemented.
> MQTT publishes asset update discovery/state and optional minimal Insights. A bounded local Insight JSON download is implemented. Event/action export, general JSON export, webhooks and REST push remain planned.



V1:

* MQTT

Later, for example in V2:

* Webhook
* General JSON Export (bounded Insight download is implemented)
* REST Push


---

Plugin type:

`Export`


---

Input:

```none
Events
Insights
Actions
Assets
```


---

Output, for example:

`MQTT Topics`


---

Example:

```none
event.security.ban
↓
opensecdash/events/security/ban

asset.update_available
↓
opensecdash/assets/update

insight.wordpress_scan
↓
opensecdash/insights/wordpress_scan
```


---

## Settings:

for example:

```none
Broker
Port
Username
Password
Topic Prefix
```


---

Example:

`opensecdash`


---

Topics:

```none
opensecdash/events/#
opensecdash/assets/#
opensecdash/actions/#
opensecdash/insights/#
```


---

### Home Assistant Integration Plugin

Optional core plugin.


---

Function:

```none
Asset Update
↓
MQTT Discovery Entity
```


---

---

## Implementation notes (2026-07-09)

MQTT asset update publishing is implemented as the current export plugin. General webhook, event/action and REST push exports remain planned.


## Insight export (2026-11-01)

`GET /api/insights/export` is a local download with the same viewer permission and
events feature gate as Insight details. It defaults to 24 hours, accepts a positive
UTC-normalized period of at most 30 days and a limit of 1–500. Descending keyset
pagination uses event time (creation time for legacy rows) and ID. Each opaque
cursor preserves its period and type filter. The existing display-time index is reused.

JSON and MQTT share a version-1 serializer and an 8 KiB per-Insight limit. The
allowlist includes ID, type, fixed title, level, confidence, derived certainty,
event/creation times, and bounded evidence counts/windows. Titles for built-in and
bundled types come from shipped definitions, never stored or remote free text.
Other types become `other.<24 hex SHA-256 characters>` with title `Insight` in
both transports; this stable mapping prevents arbitrary metadata and MQTT topic
characters from leaving the instance. It is not an anonymity guarantee for guessable
type IDs. JSON additionally contains at most 20 stored example IDs and a local
detail path, even if retention has removed those examples. No raw payloads,
descriptions, IPs, hostnames, request paths, asset names or arbitrary JSON are exported.

MQTT Insight publishing is separately opt-in (`publish_insights=false`) and also
requires the MQTT master switch. It uses the configured broker, credentials, TLS
policy and `<prefix>/insights/<safe-type>` topics, QoS 1, and `retain=false`, without
Home Assistant discovery entities. It reads committed Insights in the existing
periodic worker, never during event ingestion. Auto mode checks every 60 seconds;
positive intervals use the existing schedule; interval 0 disables automatic Insight
publishing. Each tick reads at most 100 IDs after the persisted cursor.

Activation and reactivation atomically set the cursor to the current highest ID;
there is no historical replay. The persisted activation state survives restart.
Settings transitions are observed even when the master switch is disabled. The
cursor advances only after successful batch delivery. Failures keep it unchanged
and surface through plugin diagnostics. A crash between publish and cursor commit
can repeat messages: delivery is at least once, and consumers deduplicate on Insight
ID. Asset/update publishing retains its existing modes and retained messages.
