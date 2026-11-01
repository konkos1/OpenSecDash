# ADR-015: Event Correlation

> **Implementation status (2026-07-12):** Implemented.
> - Deterministic rule-based correlations cover IP, asset, path, and time-window views.
> - Pattern rules can correlate one path across multiple IPs; plugins can register validated declarative rules.
> - IP and asset views show Certain/Probable/Possible certainty, and the dashboard lists top insights.
> - Level 2 multi-asset/multi-country chains are planned for V1 only within the scope decision of 2026-10-24 below; they are not yet implemented.
> - Torblock correlation is not applicable because no Torblock plugin exists.



## Correlation keys

For V1, the following keys must be defined:

## IP

**Most important key.**

`1.2.3.4`

links for example:

* Access
* Geoblock
* CrowdSec
* Torblock


---

## Asset

Example:

`nextcloud.example.com`

links for example:

* Access events
* Errors
* Status codes
* CrowdSec events


---

## URL/Path

Example:

`/wp-login.php`

links:

* Access events
* Geoblocks
* CrowdSec scenarios


---

## Time window

Example:

`± 5 seconds`

This lets OpenSecDash detect:

```none
Access
↓
1 second later
↓
CrowdSec Ban
```


---

# Correlation levels

## Level 1 (V1)

Rule-based.

Examples:

```none
Same IP
+
same time window
```


---

```none
404
+
Geoblock Event
```


---

```none
Access
+
CrowdSec Ban
```


---

Simple, understandable, and deterministic.


---

## Level 2 (limited V1 exception; see scope decision 2026-10-24)

More complex relationships.

Example:

```none
50 Requests
↓
multiple Assets
↓
multiple countries
↓
CrowdSec Ban
```

The original V1 exclusion is superseded only for the relationships in the scope decision of 2026-10-24 below.


---

# Insight level

Not every correlation should be treated the same.

Example:

## Certain

```none
CrowdSec Ban
+
same IP
+
1 second difference
```


---

## Probable

```none
404
+
Geoblock
+
same IP
```


---

## Possible

```none
404
+
many requests
```


---

This should then be visible:

```none
✓ Certain

≈ Probable

?
Possible
```


---

# Why this matters

This directly affects:

## IP Explorer

```none
IP: 1.2.3.4

Insights:

✓ Banned by CrowdSec

≈ 404 probably caused by geoblock

≈ Scan attempt on WordPress
```


---

## Asset Explorer

```none
Nextcloud

404:
123

Main cause:
Geoblock
```


---

## Dashboard

Then it could say there, for example:

```none
Top Insights today

37x Geoblock caused 404

12x CrowdSec Ban after scan attempt

4x Torblock triggered
```


---

## Scope decision (2026-10-24)

Level 2 correlation is brought forward into V1 for deterministic scan/ban relationships:
HTTP probing by multiple IPs across multiple assets and observed countries, followed
by an observed CrowdSec ban of a participating IP. This limited extension is
**planned, not yet implemented**.
It supersedes the earlier Level 2 exclusion only for these relationships. Temporal
proximity does not prove causality, a shared campaign, successful exploitation, or a
currently active ban. Raw events remain unchanged and findings must explain their evidence.

This decision does not authorize Torblock, LLM features, additional plugins in general,
or other previously excluded V1 features. Rules remain validated declarative data under
the existing security and plugin boundaries.


## Scope clarification (2026-11-01)

Level 1 keeps its same-IP relationships. The Level 2 exception permits multiple
IPs to contribute to a rule's path-pattern family. The ban must still belong to
an IP with a matching request within the configured follow-up interval. The
shared family and observed countries do not establish a common identity or campaign.
This clarifies the authorized scope; the overall rollout status above remains pending.
