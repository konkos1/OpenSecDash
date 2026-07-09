# ADR-016 Action Framework (Permissions for Actions)

> **Implementation status (2026-07-09):** Partially implemented.
> Central action validation, confirmation checks, global-IP validation, action records, and plugin execution exist. A full asynchronous action queue is not implemented.


## Goal

OpenSecDash should not only be able to observe.

It should be able to execute controlled actions.

Examples:

```none
CrowdSec Ban
CrowdSec Unban
Send webhook
Clear cache
Restart container
```


---

# Basic principle

Actions are plugins.

Not:

```none
UI
↓
Shell Command
```

But:

```none
UI
↓
API
↓
Action Framework
↓
Action Plugin
↓
Execution
```


---

# Action Definition

Every action registers:

```python
id
name
description
target_types
```

Example:

```python
id = "crowdsec_ban"
name = "CrowdSec Ban"
target_types = ["ip"]
```


---

# Targets

V1:

```none
IP
Asset
Event
```


---

Examples:

## IP

`1.2.3.4`

Actions:

```none
Ban
Unban
Whois
Reverse DNS
```


---

## Asset

`Nextcloud`

Actions:

```none
Show logs
Healthcheck
```


---

## Event

`CrowdSec Ban Event`

Actions:

```none
Details
Unban
```


---

# Security checks

These should **always be performed centrally**, not in the plugin.


---

## IP actions

Before ban:

```python
is_global
```

must be:

```python
True
```


---

Not bannable:

```none
127.0.0.1
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16

fc00::/7
fe80::/10
::1
```


---

This means **no** plugin can accidentally ban:

`192.168.1.1`


---

# Confirmation requirement

V1:

## Non-critical

Execute directly.

Example:

```none
Whois
Reverse DNS
```


---

## Critical

Confirmation required.

Example:

```none
CrowdSec Ban
CrowdSec Unban
```


---

Dialog:

```none
Ban IP?

1.2.3.4

Duration:
4h

[Cancel]
[Confirm]
```


---

# Audit Log

Even without user management.

Still log everything.

Table:

`action_log`


---

Example:

```none
Time
Action
Target
Result
```


---

```none
2026-06-19 13:22

crowdsec_ban

1.2.3.4

success
```


---

# Standard actions V1

## CrowdSec

```none
Ban 4h
Ban 24h
Ban 7d
Unban
```


---

## IP Explorer

```none
Whois
Reverse DNS
GeoIP Refresh
```


---

# Why this matters

This lets later plugins simply say:

`register_action(...)`

and OpenSecDash automatically creates:

* API endpoint
* Confirmation dialog
* Audit entry
* UI button

without every plugin having to implement it again.


---

---

## Implementation notes (2026-07-09)

The current implementation performs central validation for critical IP actions, rejects non-global IP targets, records actions in the `actions` table, supports confirmation requirements, and delegates execution to the owning plugin.

A full background action queue is not implemented. Actions are currently protected by in-process locks and executed through the service/plugin path.

