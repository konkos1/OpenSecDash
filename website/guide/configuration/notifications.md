# Notifications

OpenSecDash can send an email when an event or insight matches a notification rule:

```text
Event or insight → rule → email
```

The **Notifications** page shows pending, sent, and failed deliveries, the recent
history, and the built-in rules. Email is the only notification channel in V1.

## Configure SMTP

Open **Settings → Notifications (Email)** and fill in the SMTP details for your
mail server:

1. Enable **Notifications**.
2. Set the **OpenSecDash base URL**, for example `http://192.168.1.10:8000`.
   OpenSecDash uses it for links back to the IP Explorer, Events, Assets, or
   Diagnostics page. Leave it empty if you do not want links in emails.
3. Enter the SMTP host and port. `587` with STARTTLS is a common choice;
   `465` is commonly used with SSL/TLS.
4. Choose the security mode required by your mail server: none, STARTTLS, or
   SSL/TLS.
5. Enter an SMTP user and password when your server requires authentication.
   For a provider account, use an app password rather than your normal login
   password when the provider supports it.
6. Set a sender address and the recipient address for alerts.

For a homelab, the SMTP host might be a local relay such as
`mail.home.arpa`, or the SMTP server from your email provider. Do not put real
passwords in compose files, screenshots, or shared configuration examples.

The SMTP password is stored encrypted in the database. See
[Secrets are encrypted at rest](./settings.md#secrets-are-encrypted-at-rest)
for how OpenSecDash manages setting encryption keys.

## Test the configuration

Use **Send test** on the Notifications page after saving the settings. The test
uses the configured sender and recipient and adds a history entry. If SMTP is
not configured or the server rejects the message, the page shows a failure and
the history keeps the error text for troubleshooting.

## Default rules

OpenSecDash creates these rules on startup. You can enable or disable each one
on the Notifications page without losing that choice after a restart.

| Rule | Trigger | Default cooldown |
| --- | --- | --- |
| CrowdSec ban | `security.ban` event | 1 minute |
| Scanner detected | high or critical insight | 5 minutes |
| Asset offline | a system becomes stale | 60 minutes |
| Plugin error | a plugin diagnostic changes to error | 60 minutes |

## Cooldown, window, and threshold

The cooldown limits how often a rule sends mail. Further matching events stay
pending during the cooldown and are sent together as one digest afterwards.

The window is the time span used for aggregation and optional thresholds. A
threshold of three means a rule waits for three matching entries inside its
window. If the threshold is not reached before the window expires, those entries
are marked skipped instead of sending an alert.

To avoid a mail flood during a first import of an existing log, OpenSecDash does
not create notifications for events older than 15 minutes. Duplicate events do
not notify either.

## Delivery behavior

OpenSecDash sends email in a background loop, never while an event is being
stored. Failed deliveries are recorded in history and are not retried
automatically in V1; the next matching event can create a new delivery.
