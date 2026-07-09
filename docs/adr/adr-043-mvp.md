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

### ADR-015 → Correlation level 2

### ADR-021 → Event attachments

### Additional languages (except DE, EN)

### ADR-025 → Offline mode

### ADR-026 → PWA → Offline operation + Push Notifications

### ADR-033 → TorBlock

---

## Implementation notes (2026-07-09)

The current implementation does not include a Torblock plugin. Proxmox Assets is implemented. The rest of the MVP boundary should continue to be treated as a scope guard unless intentionally changed by a user request and reflected in ADR updates.

