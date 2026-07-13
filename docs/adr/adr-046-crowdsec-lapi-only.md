# ADR-046: CrowdSec LAPI-only integration

> **Implementation status (2026-07-13):** Implemented.

## Status: implemented (2026-07-13)

## Context

The CrowdSec plugin previously supported two ways to synchronize decisions and execute ban/unban actions: direct Local API requests and launching a configurable binary as a subprocess. The subprocess mode required mounting a host executable and CrowdSec configuration into the OpenSecDash container. Its configurable executable path also created an unnecessary code-execution primitive if an attacker gained settings access together with a way to place or select a suitable executable.

The Local API provides all behavior OpenSecDash needs with dedicated, revocable machine credentials and without executable or CrowdSec configuration mounts.

## Decision

- The CrowdSec plugin communicates with CrowdSec exclusively through LAPI.
- The connection-mode and executable-path settings are no longer exposed or read. Existing stored values are ignored after upgrade.
- LAPI base URLs must use HTTP or HTTPS and include a host.
- Embedded URL credentials, query strings, fragments, invalid ports, and whitespace are rejected.
- LAPI requests do not follow redirects. Login credentials and bearer tokens are therefore sent only to URLs derived from the configured base URL.
- Private, loopback, Docker, LXC, VLAN, and public hosts remain supported. OpenSecDash does not impose a built-in host or network allowlist because valid self-hosted topologies vary widely.

This decision supersedes the subprocess-based CrowdSec examples in ADR-007, ADR-013, ADR-020, ADR-028, ADR-029, ADR-033, and ADR-037.

## Consequences

- Existing installations using the removed subprocess mode must create dedicated CrowdSec machine credentials and configure the LAPI URL, login, and password.
- Same-host Docker installations continue to work with `http://127.0.0.1:8080` when host networking is enabled.
- Bridge-network installations use a reachable host address or the CrowdSec container name on a shared Docker network.
- Administrators that require stricter egress policy can enforce a host or network allowlist outside OpenSecDash, for example with container networking or firewall rules.
