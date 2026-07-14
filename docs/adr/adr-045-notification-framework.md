# ADR-045: Notification Framework

> **Implementation status (2026-07-12):** Implemented.
>
> V1 implements SMTP email only. The channel abstraction is ready for future
> channels, but Telegram, Discord, Matrix, Gotify, ntfy, Slack, Teams and
> Pushover remain intentionally unimplemented.
> SMTP messages contain a plain-text fallback and a branded HTML alternative;
> the OpenSecDash logo is embedded in the message instead of loaded remotely.
>
> Actions and system states enter the engine as events (`action.*`,
> `system.plugin_error`, `system.asset_offline`). Anti-spam uses a per-rule
> cooldown plus digest aggregation; events older than 15 minutes do not notify.
> Notification history additionally stores `subject`, `error` and `sent_at`.
>
> Asset-offline detection keeps its previous state in memory, so a restart can
> report an already-offline system again; the rule cooldown limits this. Failed
> sends are not retried automatically in V1.

## Goal

OpenSecDash should be able to actively report events, actions, and insights.


---

# Principle

Not:

```none
Plugin
↓
Telegram

Plugin
↓
Discord

Plugin
↓
Email
```

But:

```none
Event
↓
Notification Engine
↓
Channel
```


---

# Architecture

```none
Event
Insight
Action

↓

Notification Rule

↓

Notification Channel

↓

Telegram
Discord
Email
Webhook
Matrix
```


---

# Notification Sources

## Events

Example:

`security.ban`


---

## Insights

Example:

`WordPress scanner detected`


---

## Actions

Example:

`IP manually banned`


---

## System

Example:

`Plugin error`


---

# Notification Rule

Example:

```json
{
  "source": "security.ban",
  "channel": "telegram"
}
```


---

# Conditions

## Event type

`security.*`


---

## Country

```none
RU
CN
```


---

## Asset

`Nextcloud`


---

## Severity

```none
warning
error
critical
```


---

## Threshold

Example:

```none
more than 50 geoblocks
within 10 minutes
```


---

# Notification Channels V1

## Email

Implementation:

`Settings`\n`↓`\n`SMTP Server`\n`SMTP Port`\n`User`\n`Password`\n`Sender`\n`Recipient`


---

Rules:

`CrowdSec Ban`\n\n`Scanner detected`\n\n`Asset offline`\n\n`Plugin error`


---

This is fully sufficient for V1.


---

## Not V1

`Telegram`\n`Discord`\n`Matrix`\n`Gotify`\n`ntfy`\n`Slack`\n`Teams`\n`Pushover`


---

Prepare architecture:

Yes.

Implement:

No.


---

# Notification Templates

Example:

```none
CrowdSec Ban

IP: 1.2.3.4
Country: RU
Reason: HTTP Probing
Duration: 4h
```


---

# Deep Links

Every message contains links.

Example:

`Open IP Explorer`


---

or:

`Show event`


---

# Anti-Spam

**Very important.**


---

## Cooldown

Example:

```none
maximum
one message
per rule
per minute
```


---

## Aggregation

Example:

Not:

`50 messages`

But:

```none
50 geoblocks
in the last 10 minutes
```


---

# Dashboard

Dedicated page:

`Notifications`


---

shows:

```none
Sent
Failed
Pending
```


---

# Notification History

New table:

`notifications`


---

Fields:

```sql
id
timestamp
rule_id
channel
status
payload
```


---

# Test Function

Every channel gets:

`Send test`


---

Example:

```none
Telegram Test

↓

Message sent successfully
```


---

# Result

This creates:

```none
Event
↓
Insight
↓
Notification Rule
↓
Channel
↓
User
```
