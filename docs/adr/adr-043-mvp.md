# ADR-043: MVP

> **Implementation status (2026-07-09):** Partially implemented.
> The MVP boundaries are broadly followed, but Torblock is not implemented and Proxmox Assets/update-related functionality is present.



## What MUST be included in v1.0? 

```none
All ADR-xxx, but excluding the points from "What MUST NOT be included in v1.0?"
```

## What MUST NOT be included in v1.0?

Anywhere something like "Not in V1" appears

And:

### Event types:

```none
FAIL2BAN
PFSENSE
OPNSENSE
ADGUARD
PIHOLE
```

### Plugins:

```none
Nginx
Caddy
Pi-hole
AdGuard
Torblock
Nextcloud
Webhook
```

### ADR-015 → Correlation level 2 (except the scope decision of 2026-10-24 below)

### ADR-021 → Event attachments

### Additional languages (except DE, EN)

### ADR-025 → Offline mode

### ADR-026 → PWA → Offline operation + Push Notifications

### ADR-033 → TorBlock

---

## Implementation notes (2026-07-09)

The current implementation does not include a Torblock plugin. Proxmox Assets is implemented. The rest of the MVP boundary should continue to be treated as a scope guard unless intentionally changed by a user request and reflected in ADR updates.


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
