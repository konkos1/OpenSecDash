# ADR-015: Event Correlation

> **Implementation status (2026-07-12):** Implemented.
> - Deterministic rule-based correlations cover IP, asset, path, and time-window views.
> - Pattern rules can correlate one path across multiple IPs; plugins can register validated declarative rules.
> - IP and asset views show Certain/Probable/Possible certainty, and the dashboard lists top insights.
> - Level 2 multi-asset/multi-country chains remain deliberately out of scope for V1.
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

## Level 2 (later, possibly in v2)

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

This should deliberately **not** be done for V1.


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
