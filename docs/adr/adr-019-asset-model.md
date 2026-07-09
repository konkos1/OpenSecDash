# ADR-019: Asset Model

> **Implementation status (2026-07-09):** Implemented.
> System and Asset models exist with source identity, active/inactive handling, host/version/update fields, release URLs, MQTT publish flag, and explorer pages.



## Goal

An asset represents a service, application, or endpoint that a homelab user consciously operates.


---

# What is an asset?

Typical examples:

```none
Home Assistant
Nextcloud
Vaultwarden
Immich
Jellyfin
Traefik
Portainer
Grafana
Uptime Kuma
```


---

Not automatically assets:

```none
Docker Container
LXC Container
VM
Host
IP address
```

These can later be sources of an asset, but they are not the asset itself.


---

# Why?

The user normally thinks:

`"My Nextcloud has problems"`

not:

`"Container 4f7a83c has problems"`


---

# Asset types V1

## Application

Example:

```none
Nextcloud
Vaultwarden
Immich
Home Assistant
```


---

## Infrastructure

Example:

```none
Traefik
CrowdSec
AdGuard
Pi-hole
```


---

## Custom

For everything else.

Example:

```none
Internal API
Custom development
Monitoring Endpoint
```


---

# Asset model

Minimal:

```python
Asset
```

Fields:

```python
id
system_id
name
type
description
enabled
```


---

Additional fields:

```python
system
is_active
last_seen
hostname
url
version
latest_version
icon
tags
release_api_url
release_web_url
update_check_type
update_available
```



---

# Relationships

An asset can have multiple endpoints.

Example:

```none
Nextcloud
↓

cloud.example.com
cloud.internal.lan
```


---

An asset can have multiple IPs.

```none
Nextcloud 
↓ 

192.168.1.20 
10.10.10.20
```


---

An asset can have multiple routers.

```none
Nextcloud 
↓ 

/ 
/remote.php 
/status.php
```


---

# Asset Explorer

Example:

`Nextcloud`

shows:

## Overview

```none
Version
Update available
Status
```

## Accesses

```none
24h Requests
7-day Requests
30-day Requests
```

## Errors

```none
404
403
500
502
```

## Security

```none
CrowdSec Events
Geoblocks
Torblocks
```

## Top URLs

```none
/status.php
/remote.php
/login
```


---

# Automatic asset detection

V1 should deliberately be kept small.

Not:

```none
Scan Docker
Scan Kubernetes
Scan network
```


---

But:

`JSON Assets Plugin`

provides:

`Asset`


---

Example:

```json
{
  "name": "Nextcloud",
  "version": "32.0.1"
}
```

↓

Asset is created or updated.


---

# Asset deletion

**Important**:

If an app disappears:

```json
JSON Assets
↓
Nextcloud missing
```

not delete immediately.

Instead:

```json
Asset Status:
inactive
```


---

This preserves:

* History
* Trends
* Statistics

until the retention period deletes the data.


---

# Asset vs. IP Explorer

The two complement each other:

## IP Explorer

Question:

`What did this IP do?`


---

## Asset Explorer

Question:

`What happened to this application?`


---
