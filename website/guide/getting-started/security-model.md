# Security model

OpenSecDash is designed as an internal homelab admin tool.

## No built-in authentication yet

OpenSecDash currently does not include built-in user management or authentication. Treat it like a sensitive internal dashboard.

Recommended placement:

- LAN-only
- VPN-only
- behind Authentik, Authelia, Pocket ID, or another trusted forward-auth layer

Do not expose it directly to the public internet.

## Sensitive data

OpenSecDash can display:

- public and internal IP addresses
- hostnames
- asset names
- access logs
- security events
- action controls such as CrowdSec ban/unban

Review debug reports before attaching them to public issues.

## Action simulation

Action simulation / dry-run mode is the safer default. It records actions without executing them. Disable dry-run only after you have verified plugin configuration and permissions.

## Action controls

Critical actions such as CrowdSec ban and unban require confirmation. OpenSecDash centrally rejects private, local, and otherwise non-global IP targets before a critical IP action reaches a plugin. Action simulation is enabled by default, so buttons can be tested without changing CrowdSec; disable **Action simulation** in Settings only when the plugin connection and permissions are ready.

Results are recorded in Diagnostics under **Recent actions** and in the Events view. Generic actions emit `action.executed` or `action.failed`; CrowdSec uses its existing specific success event types for completed Ban/Unban actions and the shared `action.failed` event for failures.
