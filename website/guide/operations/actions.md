# Actions and safety

OpenSecDash can respond to findings as well as display them. Every external change goes through the central Action Framework instead of being executed directly by a page.

```text
UI → validation → confirmation and permission checks → plugin → target system → audit history
```

The current built-in action workflow supports CrowdSec ban and unban through the CrowdSec Local API (LAPI).

::: danger Authentication and HTTPS are required for real integrations
Do not enable real CrowdSec Ban/Unban, Proxmox, or MQTT operations on an unprotected
dashboard. Before using them, require either
[internal sign-in](../configuration/authentication.md) or an external authentication
provider, and expose OpenSecDash exclusively through an HTTPS
[reverse proxy](../installation/reverse-proxy.md) with a browser-trusted certificate.
LAN reachability alone is not an authentication boundary.
:::

## Safety controls

OpenSecDash applies the same controls regardless of which page started an action:

- Plugins register supported action types, target types, parameters, and permission requirements.
- Critical actions require explicit confirmation.
- IP actions validate and normalize their target before plugin execution.
- Private, loopback, link-local, reserved, multicast, and otherwise non-global IPs cannot be sent to critical ban actions.
- Viewer accounts cannot execute actions. Operator or Admin access is required when internal sign-in is enabled.
- Action simulation is enabled by default.
- Every attempt receives a status and is visible in Diagnostics under **Recent actions**.

These checks happen centrally before the CrowdSec plugin receives the request.

## Action simulation

With **Action simulation** enabled, an action follows the same UI, target validation, confirmation, and audit path but does not change CrowdSec. This is the recommended way to verify permissions and investigation workflows after installation.

To enable real execution:

1. Configure CrowdSec LAPI and verify its status.
2. Confirm that internal roles and network access match your intended operators.
3. Disable **Action simulation** in Settings.
4. Test with a controlled public IP target.

Do not use a private address as a test target: it is rejected by design.

## CrowdSec ban and unban

A manual ban is available from the IP Explorer for a valid public IP. Duration is selected from the action's registered parameters. A successful real action is synchronized back from LAPI and appears in the CrowdSec view and event history.

Unban is offered only when OpenSecDash knows the corresponding active CrowdSec decision ID. If the button is missing, refresh the CrowdSec decision status and check the `crowdsec · lapi` diagnostic.

The old configurable `cscli` subprocess mode is no longer supported. See [CrowdSec](../plugins/crowdsec.md) for the LAPI setup and migration rationale.

## Audit and troubleshooting

Diagnostics records the action target, type, status, timestamps, and any error returned by the plugin. Completed and failed actions also create normalized security events:

- successful CrowdSec actions use their specific manual ban/unban event types
- generic successful actions use `action.executed`
- failures use `action.failed`

If a real action fails, check:

1. `crowdsec · lapi` in Diagnostics
2. the CrowdSec LAPI URL and dedicated machine credentials
3. whether Action simulation is still enabled
4. the current user's role
5. the target IP classification
