# ADR-030: Correlation Engine

> **Implementation status (2026-11-01):** Implemented.
> - Stored insights use deterministic rules and fixed local count comparisons; remote rules are validated declarative data and never execute code.
> - Path patterns can correlate multiple IPs, and plugins register rules through the same validation path.
> - Certainty levels, a layoutable Top Insights dashboard widget, and the asset most-frequent-finding view are visible in the UI.
> - Bounded Level 2 scan/ban chains are implemented under the limited V1 scope decision of 2026-10-24 below.
> - Torblock correlation is not applicable because no Torblock plugin exists.



## Goal

OpenSecDash should automatically link events together.

Not simply:

`Display events`

but:

`Understand events`


---

# Principle

**Correlations never change raw data.**

Only additional findings are generated.


---

Not:

```none
Access Event
↓
replace
```

But:

```none
Access Event
↓
generate insight
```


---

# Architecture

```none
Event
↓
Correlation Engine
↓
Insight
```


---

# Insight types

## Nearby access failure

Example:

```none
404
+
Geoblock
↓
Access failure near GeoBlock
```


---

## Relationship

Example:

```none
CrowdSec Ban
↓
Search:
latest requests from the same IP
↓
Insight:
Ban followed HTTP probing
```


---

## Pattern

Example:

```none
100 different IPs
↓
/wp-login.php
↓
Insight:
WordPress scan detected
```


---

# Insight model

New entity:

`Insight`


---

Fields:

```python
id
timestamp
type
confidence
title
description
```


---

Additionally:

```python
related_events
```

List of involved events.


---

# Confidence

Confidence is a fixed heuristic score, not a calibrated probability. Severity is separate.


---

Values:

```none
low
medium
high
```


---

or internally:

`0.0 - 1.0`


---

Example:

```none
0.95
↓
High Confidence
```


---

# Example

Access:

```none
1.2.3.4
404
/wp-login.php
```

10 seconds later:

```none
Geoblock
1.2.3.4
```

Insight:

`Access failure near GeoBlock`

Confidence:

`0.8`


---

# Correlation Rules

Rules are stored separately.


---

Example:

```none
access.error
+
security.geoblock
within 60 seconds
↓
blocked_request
```


---

The user does not see this.


---

Plugins can register rules.


---

# Examples V1

## Geoblock correlation

```none
Access
+
Geoblock
```


---

## CrowdSec correlation

```none
Access
+
Ban
```


---

## Torblock correlation (not implemented)

```none
Access
+
Torblock
```


---

## Multiple requests

```none
50 Requests
+
same URL
+
short time
↓
Scanner detected
```


---

# IP Explorer

This is where the Correlation Engine becomes especially visible.


---

Example:

```none
IP
1.2.3.4
```

Not only:

`Events`

But for example:

```none
Insights

CrowdSec Ban triggered

WordPress scan detected

403 by geoblock
```


---

# Asset Explorer

Likewise.

Example:

`Nextcloud`

Insights, for example:

```none
Multiple 404 on /remote.php

CrowdSec reaction triggered

Count rising for 7 days
```


---

# Dashboard Widget

Later, for example:

`Top Insights`


---

Examples:

```none
WordPress scanner detected

CrowdSec reacts to HTTP probing

Increase in geoblocks from CN
```


---

# Storage

Insights are stored.

Not recalculated every time.


---

New table:

`insights`


---

Why?

Later:

```none
30 days
90 days
1 year
```

insights must be displayed quickly.


---

# Explainability

**Extremely important.**


---

Every insight must explain:

`Why?`


---

Example:

```none
WordPress scan detected

Reason:

42 accesses to /wp-login.php
from 8 IPs
within 5 minutes
```

Not, for example:

`AI says so`


---

# V1 deliberately rule-based

**No AI.**

**No LLMs.**

**No black box.**


---

# Remote code execution prevention

The Insights engine must actively prevent remote code execution.

Remote insight updates may only be accepted as declarative, validated data.

Not allowed:

```none
remote Python code
eval
dynamic imports
user-configurable remote rule sources
executable rule payloads
```

Allowed:

```none
validated JSON rules
schema-version checks
field and size limits
local fallback rules
```

This keeps rule updates predictable and prevents the Insights engine from becoming a generic remote-code loader.


---

Rules:

```none
If A + B
then Insight C
```

Advantages:

* understandable
* fast
* testable
* reproducible


---

# Result

This creates the actual value:

```none
Logs
↓
Events
↓
Correlation
↓
Insights
↓
User
```

---

## Implementation notes (2026-08-26)

The direct Insight type `asn_policy_security_ban` is created only from a successful
`security.ban.asn_policy` event. It has high confidence and records the matching ASN,
ASN-organization snapshot, and seven-day duration. Dedupe uses the related source event ID,
so one successful automatic ban produces one Insight.

This is direct event evidence rather than a new heuristic or remote rule. Its wording is
deliberately historical: it states that the IP was banned because of the permanent ASN
policy and does not claim that the CrowdSec decision is still active. Current state is
read from the synchronized CrowdSec decisions.


---

## Scope decision (2026-10-24)

Level 2 correlation is brought forward into V1 for deterministic scan/ban relationships:
HTTP probing by multiple IPs across multiple assets and observed countries, followed
by an observed CrowdSec ban of a participating IP. This limited extension is
**implemented as of 2026-11-01**, within the bounded contract in ADR-030.
The bundled app ruleset includes `web.distributed_scan_then_ban`; public schema-2
publication remains gated on a verified compatible app release.
It supersedes the earlier Level 2 exclusion only for these relationships. Temporal
proximity does not prove causality, a shared campaign, successful exploitation, or a
currently active ban. Raw events remain unchanged and findings must explain their evidence.

This decision does not authorize Torblock, LLM features, additional plugins in general,
or other previously excluded V1 features. Rules remain validated declarative data under
the existing security and plugin boundaries.


## Event time and retained evidence (2026-11-01)

All built-in and declarative findings use the shared `record_insight()` storage helper.
`timestamp` remains creation time; nullable `occurred_at` is the event time. Direct
facts use their source event time and `<type>:event:<id>` identity. Relationships
use the later event time and `<type>:pair:<smaller-id>:<larger-id>`, with sorted IDs.
Declarative findings use the end of the evaluated event-time window.

IP rule keys use `ip:<canonical-ip>`; schema-1 path groups use the global key `rule`.
Cooldowns query only the same type/key in the closed interval `[t-window, t]`.
Future findings cannot suppress an older separate wave. Direct facts have no time
cooldown. Legacy findings without event time deduplicate only identical event IDs;
their creation time is never substituted into historical cooldowns. Unique dedupe
keys handle competing inserts within a savepoint. Insight generation also has an
outer savepoint so a failed rule cannot invalidate ingestion of a valid event.
GeoIP may update a GeoBlock description without a second finding or notification.
Notifications use event time and retain the 15-minute backlog protection.

Evidence version 1 is a closed JSON summary, at most 8 KiB in UTF-8. Required fields
are `version` (integer 1) and `kind` (`direct`, `correlation`, `heuristic`, `trend`).
The storage helper requires `sample_count` to match the sorted, unique sample of
at most 20 positive event IDs. Producers also supply `sample_truncated` (boolean).
Optional fields are:

- `rule_id`, `rule_source`, `ruleset_version`: nonempty strings identifying the rule snapshot.
- `window_start`, `window_end`: explicit UTC ISO timestamps.
- `event_count`, `distinct_ip_count`, `distinct_asset_count`, `distinct_country_count`,
  `sample_count`, `followup_seconds`, `followup_gap_seconds`: nonnegative integers.

Unknown fields and invalid types are rejected. Trend evidence additionally accepts
the closed version-1 `comparison` object described below. Missing optional information is omitted,
not inferred. Raw rows, full IP/URL lists, secrets and arbitrary payloads are not
snapshot fields. Declarative evaluation uses SQL counts and a bounded deterministic
ID sample. Evidence survives raw-event retention without copying raw data. Existing
findings retain their IDs and texts with NULL event time/evidence; no backfill or
startup sweep attempts to reconstruct deleted data.


## Scope clarification (2026-11-01)

Level 1 keeps its same-IP relationships. The Level 2 exception permits multiple
IPs to contribute to a rule's path-pattern family. The ban must still belong to
an IP with a matching request within the configured follow-up interval. The
shared family and observed countries do not establish a common identity or campaign.
The app implementation is complete; publication of the public schema-2 ruleset remains a separate release gate.


## Observed count trends (2026-11-01)

Two fixed local types compare raw `access.error` counts per known asset and raw
`security.geoblock` counts per known country over two completed seven-day UTC windows.
A finding requires a previous count of at least 50, at least twice that count now,
and an absolute increase of at least 50. Country rollups are not an input.

The source signature and observation start are persisted separately for each metric.
Changed enabled plugin/source IDs restart the 14-full-day observation period. Raw
retention must cover the concrete comparison window or be unlimited. Its start must
be at or after the current time minus the retention duration, including when an
older daily cycle resumes. Exactly 14 days therefore suffice only at its midnight
cutoff; current source errors skip evaluation.
These checks do not establish continuous historical coverage. Findings explicitly
report observed counts; import gaps and logging changes can explain increases.

An hourly thread worker processes at most 100 groups with keyset pagination and a
persisted daily cutoff. Each type/dimension/date has a stable dedupe key; later backlog
does not rewrite successful findings. Notifications remain opt-in with the existing
15-minute protection. No retention extension, automatic actions or external service
is introduced.

`comparison` contains integer `version=1`, UTC ISO `previous_start`, `previous_end`,
`current_start`, `current_end` (adjacent seven-day half-open intervals), nonnegative
integer `previous_count`, `current_count`, `difference`, numeric `factor`,
`dimension` (`asset` or `country`), `dimension_value` (positive asset ID or known
uppercase country code), and `coverage="observed_only"`. Counts, difference and factor
must agree; a zero previous count cannot form a comparison. The snapshot keeps at
most 20 newest current-window example IDs. Event time is the current window's end,
level is medium and confidence is 0.7. Displayed intervals use the central timezone
filter; Dashboard today retains its existing local-day semantics.


## Bounded Level 2 implementation and certainty (2026-11-01)

Schema 2 accepts `web_probe` and `scan_ban_chain` data, with literal case-insensitive
substring/exact/prefix paths and optional status/method filters. Schema 1 retains
its original semantics. Imports validate the complete payload and apply it in a
savepoint; remote overrides survive failed refreshes and bundled reseeding.

The bundled chain requires 20 denied/error requests to `/wp-login.php` or
`/xmlrpc.php`, 2 IPs, 2 known assets and 2 observed countries in a closed five-minute
window. It ends at the banned participant's latest matching request before an
automatic `security.ban`, at most 60 seconds earlier. Manual, simulated and ASN-policy
bans do not anchor chains. Unknown assets/countries do not increase distinct counts.
GeoIP describes observed locations, not identities. The finding has the ban event
time/IP, no single asset, confidence capped at 0.8, and at most 19 probe IDs plus the
ban ID. A rule/ban dedupe key and rule-wide event-time cooldown prevent repetition.

A thread worker runs every 60 seconds with at most 100 anchors and 20 available
rules per tick. Persisted event-ID and rule-ID cursors retain a fixed creation-time
window across unfinished batches and restarts. Each batch of up to 20 rules drains
the entire window before the event cursor resets for the next rule batch. All
available rules from bundled, remote and plugin sources are considered before the
cycle completes; the per-tick limit never truncates the accepted rule set. Rule
availability is checked each tick, but each running batch persists its assigned rule IDs.
Removed or unavailable rules are skipped without pulling replacement rules into the
partially processed window. The batch advances past its original final rule ID even
if all assigned rules disappear. Newly added or re-enabled rules omitted from a
running batch at or before its final ID are considered in the next cycle. Older
cycles without persisted batch IDs replay the current rule batch from its first
anchor while retaining the original window and rule cursor. The completed creation-time
cutoff is persisted atomically with the final batch. The next window starts 15 minutes before that cutoff and ends at
the current time, so long backlogs cannot leave gaps between cycles. The first cycle
uses the last 15 minutes, including recently imported historical bans. Enrichment
arriving outside that overlap receives no full-history recheck. Failures are isolated
per rule/anchor; no ban is issued.
Arbitrary multistage graphs, campaign attribution, forecasts and learned baselines
remain outside this implementation.

Level 1 reports access errors/denials near GeoBlock in either time direction within
60 seconds, or an observed ban at/after access within 60 seconds. Known asset/host
conflicts exclude pairs; confidence is 0.8 with shared context, 0.6 without it.
The closest pair wins, with smaller ID breaking ties. These are relationships, not
proven causes. Direct evidence with confidence >=0.8 is Certain; other known evidence
at that threshold is Probable; lower/legacy evidence is Possible, regardless of severity.

Details preserve counts and windows after raw retention, distinguish missing examples
from totals, and label legacy creation-time fallback. Asset Explorer counts the most
frequent finding over 24 hours rather than assigning a cause. JSON and opt-in MQTT
use the bounded serializer described in ADR-031.
