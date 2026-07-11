# ADR-016 Action Framework (Permissions for Actions)

> **Implementation status (2026-07-11):** Implemented.
> Central validation, confirmation requirements, global-IP gates, audit records/events, metadata-based action registration, and registry-driven IP Explorer actions are implemented. The asynchronous queue and user management remain outside this V1 scope.


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

## Implementation notes (2026-07-11)

- **Action scope:** Per ADR-035 (V1 Scope Freeze), V1 actions are limited to CrowdSec Ban and CrowdSec Unban. The IP Explorer examples in this ADR (Whois, Reverse DNS, GeoIP Refresh) and the Asset (Show logs, Healthcheck) and Event example actions are not implemented. The framework supports additional targets/actions generically through `target_types` and `ActionDefinition`; a future action requires a registration and a plugin `execute` hook, not framework work.
- **Audit table:** The audit table is named `actions`, consistent with ADR-029 and ADR-036, rather than `action_log` as named in the original text.
- **Action queue:** An asynchronous queue is not part of this ADR's text. Execution is synchronous and protected by in-process locks. The queue specified by ADR-029 remains open.
- **Permissions:** Each action declares a `permission` value as specified by ADR-029, but permissions are always allowed in V1. User management is explicitly not V1 per ADR-035.
