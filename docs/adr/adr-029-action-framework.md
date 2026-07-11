# ADR-029: Action Framework

> **Implementation status (2026-07-11):** Partially implemented.
> Action registration, plugin execution, confirmation requirement, dry-run, action status/history, and audit events exist. A real background action queue remains planned.



## Goal

OpenSecDash should not only display data.

It should be able to execute controlled actions.


---

# Principle

All changes to external systems happen through actions.

Not:

```json
UI
↓
Shell
```

but:

```json
UI
↓
API
↓
Action Framework
↓
Plugin
↓
Target system
```


---

# Examples V1

## CrowdSec Ban

`Ban IP`


---

## CrowdSec Unban

`Unban IP`


---

## Open URL

Example:

`GitHub Release Notes`


---

## IP Explorer

`Ban IP`

directly from the IP view.


---

# Action types

## Security

`security.ban`
`security.unban`


---

## Asset

Later:

`asset.restart`
`asset.update`


---

## Notification

Later, for example:

`notification.webhook`
`notification.discord`
`notification.telegram`


---

## System

Later, for example:

`system.restart_service`
`system.reload_plugin`


---

# Action registration

Plugin:

```python
register_action(
    id="crowdsec_ban",
    type="security.ban"
)
```

This tells the core:

`This action exists`


---

# Action Definition

Example:

```json
{
  "id": "crowdsec_ban",
  "type": "security.ban",
  "label": "Ban IP"
}
```


---

# Action parameters

Example:

```json
{
  "ip": "1.2.3.4",
  "duration": "4h"
}
```


---

# Standardization

Define fixed fields directly for security actions.

## Ban

```json
{
  "ip": "1.2.3.4",
  "duration": "4h",
  "reason": "Manual ban"
}
```


---

## Unban

```json
{
  "ip": "1.2.3.4"
}
```


---

This lets different plugins later use the same interface.


---

# Action permissions

Although V1 has no user management:

Actions should still have a permission model.


---

Example:

```json
{
  "permission": "security.ban"
}
```


---

Today:

`always allowed`


---

Later, for example in V2:

```none
Viewer
Operator
Admin
```

→ without database migration.


---

# Confirmation requirement

For critical actions.


---

## Required

```none
Ban
Unban
Delete
Restart
```

Dialog:

```none
Ban IP 1.2.3.4 for 4 hours?

[Cancel]
[Confirm]
```


---

# Action Queue

**Important.**

Not:

`UI waits for cscli`

But:

```none
Create action
↓
Queue
↓
Plugin executes
↓
Status returned
```


---

Why?

Later actions could take time, for example:

```none
Webhook
API
Container Restart
```


---

# Action Status

Every action gets a status.

```none
pending
running
completed
failed
```


---

# Action history

New table:

`actions`


---

Fields:

```none
id
timestamp
action_type
plugin_id
status
target
result
```

Example:

```none
2026-06-19

CrowdSec Ban

1.2.3.4

completed
```


---

# Dashboard

Later, its own widget:

`Latest actions`


---

Example:

```none
Ban 1.2.3.4
Unban 5.6.7.8
Webhook triggered
```


---

# Dry Run

For development.

Setting:

`Action Simulation`

Then:

`Action would be executed`

but:

`no real command`


---

→ Very helpful for plugin developers.


---

# Audit Trail

**Extremely important.**

Every action automatically creates:

`action.executed`

or

`action.failed`

Event.

This makes it appear in the event system.

Example:

```none
IP Explorer
↓ Ban
↓
Event
```

Later it can be traced:

```none
Why was this IP banned?
When?
By which plugin?
```


---

# Result

Complete write path:

```none
UI
↓
API
↓
Action Framework
↓
Plugin
↓
Target system
↓
Event
```


---

---

## Implementation notes (2026-07-11)

The current implementation supports plugin registration through `ActionDefinition` metadata (id, label, permission, target types, and standardized Ban/Unban parameters such as `ip`, `duration`, and `reason`), confirmation requirements, dry-run mode, action records/status, plugin validation and availability hooks, plugin execution, and registry-driven IP Explorer UI. The registry provides API availability, confirmation dialogs, audit records/events, and action buttons from one definition.

A persistent background action queue is not implemented yet. Execution is synchronous and guarded by in-process locks to avoid conflicting concurrent actions. The Dashboard widget `Latest actions` remains a later item; recent actions are currently shown in Diagnostics.
