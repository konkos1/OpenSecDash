# Notifications

OpenSecDash can send an email when an event, insight, or newly available asset update matches a notification rule:

```text
Event, insight, or asset update → rule → email
```

The **Notifications** page shows pending, sent, and failed deliveries, the recent history, and the available rules. Every known Insight type has its own switch. Email is the only notification channel in V1.

## Configure SMTP

Open **Settings → Notifications (Email)** and fill in the SMTP details for your mail server:

1. Enable **Notifications**.
2. Set the **OpenSecDash base URL**, for example `http://192.168.1.10:8000`. OpenSecDash uses it for links back to the IP Explorer, Events, Assets, or Diagnostics page. Leave it empty if you do not want links in emails.
3. Enter the SMTP host and port. `587` with STARTTLS is a common choice; `465` is commonly used with SSL/TLS.
4. Choose the security mode required by your mail server: none, STARTTLS, or SSL/TLS. STARTTLS and SSL/TLS verify the server certificate and hostname.
5. If the server certificate uses your own homelab CA, optionally enter the path to its PEM certificate as **Custom CA certificate file**. The file must be available inside the OpenSecDash container. Leave the field empty to use the system trust store.
6. Enter an SMTP user and password when your server requires authentication. For a provider account, use an app password rather than your normal login password when the provider supports it.
7. Set a sender address and the recipient address for alerts. The sender also accepts a display name in standard mailbox format, for example `OpenSecDash <notifications@example.com>`. Your SMTP provider may require the address or alias to be approved for the authenticated account.

For a homelab, the SMTP host might be a local relay such as `mail.home.arpa`, or the SMTP server from your email provider. Do not put real passwords in compose files, screenshots, or shared configuration examples.
The configured SMTP hostname must match the certificate. Mount a private CA certificate read-only into the container when you use the custom CA field; certificate verification cannot be disabled.

The SMTP password is stored encrypted in the database. See [Secrets are encrypted at rest](./settings.md#secrets-are-encrypted-at-rest) for how OpenSecDash manages setting encryption keys.

STARTTLS and SSL/TLS use the system trust store (plus the optional custom CA file) and verify the SMTP certificate and hostname. The unencrypted mode remains available for existing local relays, but Settings shows a warning because message contents and credentials then cross the network without transport encryption.

## Test the configuration

Use **Send test** on the Notifications page after saving the settings. The test uses the configured sender and recipient and adds a history entry. If SMTP is not configured or the server rejects the message, the page shows a failure and the history keeps the error text for troubleshooting.

## Default rules

OpenSecDash creates the event and system rules below on startup. It also creates one separate notification rule for every built-in, downloaded, or plugin-provided Insight.
You can enable or disable each rule without losing that choice after a restart or an Insight ruleset update.

All newly created notification rules start disabled. Notifications are always opt-in:
enabling the global notification setting and configuring SMTP does not activate any event, system, ASN, or Insight rule. Select each rule you want to receive explicitly on the Notifications page. Existing saved rule choices are preserved during upgrades.

| Rule | Trigger | Default cooldown |
| --- | --- | --- |
| CrowdSec ban | `security.ban` or successful permanent ASN-policy ban | 1 minute |
| ASN organization changed | a confirmed organization change for a permanent ASN policy | 60 minutes |
| Asset update available | a newly detected available version for an asset | 1 minute |
| Asset offline | a system becomes stale | 60 minutes |
| Plugin error | a plugin diagnostic changes to error | 60 minutes |

ASN-policy ban emails include the IP, ASN, current ASN organization, decision duration, CrowdSec scenario, and occurrence time. ASN-organization change emails include the ASN, previous and current organization snapshots, and the time the change was confirmed. These names are GeoIP snapshots for operator review; they do not prove that the ASN changed ownership.

Asset-update emails include the asset and system identity, installed and available versions, asset and system types, source, host URL, check time, and a direct link to the GitHub release notes when those values are available. Offline-system emails include the system identity, type, source, last-seen time, and occurrence time. A repeated check of the same available version does not create another notification; a later release does.

Plugin-error emails include the affected plugin, occurrence time, and redacted diagnostic error in the message. Insight emails include the specific title, description, confidence, occurrence time, IP, and related asset and system identities when available. All displayed payload times use the configured time zone, or UTC while automatic browser time-zone detection is selected.

Subjects intentionally remain stable per notification rule and do not contain event-specific details. This allows receiving mail clients to group messages from the same rule. The Notifications history shows the same structured details as the message body alongside the stable subject.

When upgrading from the earlier shared **Scanner detected** rule, currently known high-severity Insights inherit its enabled state and the wildcard rule is retired to prevent duplicate email.

Plugins declare the event types they can produce. An Insight rule is selectable and effective only while enabled plugins can produce all of its required input event types.
For example, **CrowdSec ban after access errors** requires both CrowdSec ban events and an enabled access-log plugin that produces `access.error`. If a required plugin is disabled or unloaded, the Notifications page keeps the saved checkbox value but disables the control and explains which producer is missing. Re-enabling the plugin restores the rule with its previous choice. Temporary plugin health errors do not change availability.

Offline detection remembers the exact asset update for which it emitted an event. Restarting OpenSecDash or running multiple workers therefore does not repeat an offline event unless the asset was seen again and later becomes stale once more.

## Cooldown, window, and threshold

The cooldown limits how often a rule sends mail. Further matching events stay pending during the cooldown and are sent together as one digest afterwards.

The window is the time span used for aggregation and optional thresholds. A threshold of three means a rule waits for three matching entries inside its window. If the threshold is not reached before the window expires, those entries are marked skipped instead of sending an alert.

To avoid a mail flood during a first import of an existing log, OpenSecDash does not create notifications for events older than 15 minutes. Duplicate events do not notify either.

## Delivery behavior

OpenSecDash sends email in a background loop, never while an event is being stored. Failed deliveries are recorded in history and are not retried automatically in V1; the next matching event can create a new delivery.

Each message contains a branded HTML version with clickable links and a plain text fallback. The OpenSecDash logo is embedded in the message; mail clients do not need to load it from an external server.
