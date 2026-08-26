# GeoIP Plugin

The GeoIP plugin enriches public IP events with:

- country
- city
- ASN
- ASN organization
- ISP

Lookups are cached to reduce provider calls. Local, private, reserved, and otherwise non-public addresses are skipped.

::: warning Remote GeoIP is disabled by default
GeoIP is opt-in. A new installation starts with the plugin disabled and pre-selects
the **IPLocate EU endpoint** provider, so nothing is looked up until you enable GeoIP
yourself.

Whichever provider you pick, every uncached public IP is sent to that provider and
processed there. Encrypted transport protects the lookup on the way — it does not make
it a local or anonymous lookup. Private, local, reserved, and otherwise non-public
addresses are never sent.

An active configuration remains active after an upgrade and is shown as a warning in
Settings and Diagnostics.
:::

## Providers

| Provider | Transport | Needs a key |
| --- | --- | --- |
| IPLocate EU endpoint | HTTPS to a fixed EU endpoint | Yes |
| ip-api.com | Unencrypted HTTP | No |

### IPLocate EU endpoint

IPLocate is the pre-selected provider for new installations. OpenSecDash always calls
the provider's fixed EU endpoint `https://eu-api.iplocate.io/`, which cannot be
changed in Settings: the address is a constant in the code, certificate verification
stays on, and redirects are not followed, so neither a misconfiguration nor a
redirected response can move a lookup — or the API key — somewhere else.

An API key is required. There is no silent fallback to the smaller anonymous quota: if
IPLocate is selected without a key, Diagnostics reports the incomplete configuration
and no lookup happens. The key field only appears while GeoIP is enabled and IPLocate
is selected.

The key is stored encrypted and sent in the `X-API-Key` request header only, never in
the URL. It is never rendered back into the page, into logs, or into a debug report;
Settings only shows whether a key is stored. Saving with an empty field keeps the
stored key, entering a new value replaces it, and a separate confirmed delete action
removes it.

OpenSecDash requests only the fields it stores — country, city, ASN number, ASN
organization, and company or hosting provider. IPLocate's threat, VPN/proxy,
abuse-contact, and coordinate data is neither requested nor stored. ASN organization is
filled from `asn.name`; ISP is filled from the company name and then the hosting provider.

A free IPLocate account was enough for a typical homelab when this integration was
added (checked 2026-08-01). Quotas and plans are the provider's decision and can
change — see [IPLocate plans and API credits](https://www.iplocate.io/docs/getting-started/plans-api-credits).

### ip-api.com

`ip-api.com` remains available as a legacy option and needs no key. It sends every
uncached public IP over **unencrypted HTTP**, so anyone on the network path can read
which addresses you look up. An installation that already uses it keeps using it after
an upgrade; it is never selected automatically, and a failing IPLocate lookup never
falls back to it.

The ASN organization is extracted from ip-api.com's combined `as` field. Its separate
`isp` field remains the IP-specific ISP value.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Adds country, city, ASN, ASN organization, and ISP to new public-IP events when the producer did not already provide them. Disabled by default. |
| Provider | GeoIP provider. The selected option shows its own transport and privacy note. |
| IPLocate API key | Required for IPLocate lookups. Only visible while GeoIP is enabled and IPLocate is selected. |
| Cache TTL days | How long successful lookups stay cached before being refreshed. 30 days by default. |
| Timeout seconds | HTTP timeout for one GeoIP provider request. |

GeoIP is only useful when at least one event-producing plugin is enabled. Diagnostics shows it as disabled when there are no event datasources to enrich.

Diagnostics never sends a separate provider probe. Instead, it reports the outcome of
the latest real lookup for the selected provider. Before the first lookup it shows that
reachability has not been verified. A failed IPLocate connection changes the diagnostic
to an error with checks for DNS, firewall, proxy, and outbound HTTPS; a later successful
lookup restores the normal active-provider warning automatically. The diagnostic includes
the attempt time but never the looked-up address, provider response, or API key.

## Caching and provider changes

Successful lookups are cached for the configured TTL; failures are cached for one hour
so an unreachable provider cannot cause a request per event. Each cache entry records
the provider that produced it, and an entry only counts as a hit for the provider that
is currently selected. After you switch providers, the stored entries of the previous
provider are therefore not served: the next lookup for that address refreshes the entry
through the new provider.

## GeoIP and permanent ASN bans

Permanent ASN policies act on the `asn` and `asn_organization` fields stored with an
event. The ASN number is the policy key; `asn_organization` is its latest display
snapshot. The separate `isp` field remains specific to the IP range and is never used as
an ASN-organization fallback. When an event producer already supplies GeoIP fields, those
values win and the remote provider does not overwrite them. A remotely resolved
organization is only paired with a producer-supplied ASN when the provider returned the
same ASN. Otherwise, the selected remote provider and its cached result supply the
classification.

The selected provider, cache TTL, and provider changes therefore affect freshness. A
successful result is reused until its TTL expires. Switching providers bypasses entries
from the previous provider, while a producer-supplied value can remain as current as the
producer makes it. GeoIP data can be wrong, stale, incomplete, or temporarily unavailable;
OpenSecDash makes no accuracy guarantee for an external provider. A wrong ASN can cause a
wrong automatic ban.

ASN allocation can be transferred, and organizations can rename or change. The stored
`asn_organization` value is not an authoritative registry name or permanent identity.
When a blocked ASN receives the same substantially different non-empty organization on
three matching observations across at least two IPs, OpenSecDash keeps the old and new
snapshots and detection time and marks the policy for manual review. Unicode, case and
whitespace are normalized for comparison; punctuation around common trailing legal forms
and legal-form variants such as `Inc`/`Inc.` are ignored conservatively. Other words and
punctuation remain significant. A return to the current organization resets a pending
candidate, and an open warning is not emitted again. The warning does not prove ownership
changed. Detection and acknowledgement leave the policy and its decisions active. Use the
separate confirmed policy-removal action if review shows that the policy itself should end.

An ASN-specific IP exception is the correction path for one false match without allowing
the IP globally. Disabling GeoIP stops new automatic classifications and policy bans, but
does not delete stored policies or exceptions. They remain visible on the CrowdSec page.

::: warning Classification follows the first event
GeoIP enrichment runs asynchronously after an event is stored:

```text
first access is stored
→ GeoIP assigns ASN, ASN organization, and ISP data
→ OpenSecDash creates a seven-day CrowdSec IP decision
→ the bouncer fetches and applies it
```

The first access cannot be blocked by the ASN policy. Blocking can happen **no earlier
than the second access**, but provider, enrichment, LAPI, and bouncer latency or errors
mean further accesses may also get through.
:::

## For contributors

Each provider lives in its own module under
`plugins/geoip/services/providers/`, and a small static registry in the same
package maps the stored provider ID to its implementation. A provider module owns only
its endpoint, headers, and response format. Public-IP filtering, caching, TTLs, the
error cache, and the producer-wins enrichment rule live once in the provider-neutral
plugin service. The core invokes it through the generic enrichment hook; providers are
never discovered dynamically or loaded from configuration.

## Display

Country, city, ASN with organization, and ISP can be enabled as optional columns in Events and Access views.
Use **Columns** to show ASN before opening its permanent-policy popup.
