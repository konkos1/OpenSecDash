# ADR-047: Permanent manual ASN bans

> **Implementation status (2026-08-26):** Implemented. The persistent data model,
> event-driven enforcement, management UI, provider-review workflow, and operator
> documentation are complete.

## Status: implemented (2026-08-26)

## Context

Operators need a durable way to classify an autonomous system as unwanted without
depending on non-standard CrowdSec behavior. CrowdSec can store freely named scopes,
but common bouncers normally remediate IP and range decisions. Expanding every BGP
prefix of an ASN would add a new intelligence dependency, produce broad decisions,
and still become stale as routing changes.

GeoIP enrichment already supplies the observed ASN and provider or organization name
for an event. That information is useful for a local policy, but it is neither
authoritative nor immutable. Provider errors, cached results, ASN transfers, and name
changes can all change the classification. The first request also has to be observed
before the policy can match it, so no design can guarantee blocking that request or
even the immediately following request.

This feature deliberately extends the original V1 scope freeze in ADR-035. Its schema
remains central alongside the existing CrowdSec decision model, while CrowdSec-owned
logic remains in the CrowdSec plugin as required by ADR-044.

## Decision

- A permanent ASN ban is a durable local OpenSecDash policy. It is not an infinite
  CrowdSec decision and is never sent as a native ASN decision.
- A matching observation creates only a global single-IP CrowdSec decision with
  `scope=Ip`, duration `7d`, and origin `opensecdash`.
- Matching is event-driven and runs only after successful GeoIP enrichment. It does
  not expand BGP prefixes, scan historical events, or renew decisions on a timer.
- GeoIP is a required classification source. Provider availability, cache age, and
  incorrect or changing provider data can cause delayed or incorrect classifications.
- The event that establishes the classification has already reached the monitored
  service. Enforcement can affect only a later request, and is not guaranteed to be
  active by the second request because enrichment, LAPI, and bouncer updates can lag.
- Policy decisions use the stable scenario prefix
  `opensecdash/manual-permanent-asn-ban/AS...`. The complete scenario remains on events
  and history records, while rollups group all such scenarios under one stable key and
  a localized CrowdSec-owned display name.
- OpenSecDash records the exact generated CrowdSec decision ID and its policy
  enforcement. Only a decision with that ownership record, scenario, and origin is a
  candidate for policy-driven removal; no operation may broadly unban an IP.
- Unbanning a policy-owned IP creates a durable exception for exactly that policy and
  canonical IP. It is not a global allowlist and does not affect independent decisions.
- If later GeoIP enrichment assigns an enforced IP to a different ASN which is not
  blocked, OpenSecDash removes only the exact old policy-owned decision. A failed
  removal remains pending for an ID-specific retry and creates no exception.
- If the new ASN is also permanently blocked, the existing policy-owned decision
  remains attributed to the old policy until it expires. Ownership is never moved
  between policies in place; only a later observation after expiry may create a
  decision attributed to the new policy.
- Each successful automatic policy ban creates a direct, high-confidence insight. The
  insight records the ban as a historical fact and is not the source of current
  CrowdSec decision state.
- ASN values use one canonical `AS` plus positive decimal 32-bit number form. IP values
  use the canonical result of `ipaddress.ip_address()` and must be global. The CrowdSec
  policy service owns this normalization contract for database values, action targets,
  scenarios, forms, URLs, and display.
- The policy, exception, and enforcement tables use uniqueness and cascading ownership
  so one ASN has one policy and one IP has at most one row of each kind per policy.
  Event and action IDs are nullable audit correlation values rather than foreign keys,
  because their retention must not invalidate enforcement ownership.
- The provider name is a mutable display snapshot, not ASN identity. A later non-empty,
  substantially different name replaces the snapshot, preserves the previous value,
  and requires operator review. Whitespace and case-only differences do not trigger a
  review. Acknowledgement clears only the review flag; no name change or acknowledgement
  pauses a policy or removes a decision.
- Policy mutations remain critical, confirmed, role-protected Action Framework
  operations with audit records. Ban operations require `security.ban`, unban operations
  require `security.unban`, and global action dry-run prevents real enforcement.
- Automatic enforcement uses an internal action path that HTTP clients cannot invoke or
  emulate. GeoIP reports completed enrichment through a small generic plugin hook; core
  and GeoIP never import the CrowdSec plugin directly.

## Rejected alternatives

- **Native `As` CrowdSec decisions:** common bouncers do not reliably enforce them.
- **Expanding all BGP prefixes:** this requires a new external intelligence flow, can
  become stale quickly, and creates much broader remediation than observed IP decisions.
- **Infinite or periodically renewed IP decisions:** expiry would no longer require a
  new observation and transient classifications could remain enforced indefinitely.
- **A global IP allowlist:** an exception must not suppress another ASN policy or an
  unrelated CrowdSec decision.
- **A historical database sweep:** activation must not retroactively ban every IP ever
  observed under an ASN.

## Consequences

- The first observed access is never prevented by this policy. Later accesses can also
  arrive before GeoIP enrichment, CrowdSec LAPI, and bouncer propagation complete.
- GeoIP and CrowdSec must both be effectively enabled for new policy enforcement.
  Existing policies and exceptions remain visible when either integration is unavailable.
- Seven-day decisions naturally expire. Only a new matching enriched event can create a
  later decision.
- Exact ownership data makes targeted unban, reclassification release, retry, and
  cleanup possible without touching community or independently created decisions.
- Provider changes remain visible for review while enforcement continues unchanged.
- A small generic post-enrichment hook expands the plugin contract without introducing
  direct cross-plugin imports or a remotely configurable event bus.

## Implementation notes (2026-08-26)

The implementation follows this decision without functional deviations. Policy-owned
decisions are synchronized through CrowdSec LAPI, their exact IDs are retained for
release and cleanup, and the UI exposes active decisions, exceptions, pending work,
provider snapshots, and review acknowledgement. The direct ASN-policy Insight is stored
as historical evidence of a successful ban; current decision state remains sourced from
the CrowdSec decision synchronization.
