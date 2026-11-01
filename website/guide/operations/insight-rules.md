# Insights engine

The Insights engine is a core OpenSecDash feature. It creates insights from these kinds of logic:

- direct facts from datasources, such as CrowdSec bans or GeoBlock denies
- heuristic web-probe rules, such as WordPress or phpMyAdmin scan patterns
- Level 2 relationships from validated scan/ban chain rules when supplied by an active ruleset

The heuristic rules are declarative JSON data. They are not executable code.

## Simple event relationships

`blocked_request` reports **Access failure near GeoBlock**: an `access.error` or
`access.denied` event and a GeoBlock event from the same IP, at most 60 seconds apart
in either event-time direction. `ban_after_access` reports **Security ban after access
failures**: an access failure precedes or coincides with an observed security ban
(`security.ban` or `security.ban.manual`) by at most 60 seconds. ASN-policy bans remain
separate direct facts. Both relationships work regardless of import order.

Known conflicting asset IDs or normalized hostnames exclude a pair. Host matching
uses the central host normalization and asset assignment; it does not infer plugin
aliases. Matching known asset or host context gives heuristic confidence `0.8`.
When only IP and time match, confidence is `0.6` and the description states the missing
context. Descriptions retain the actual HTTP status (or state that it is unknown),
time gap and shared context. Neither relationship proves causality, scanning,
successful exploitation or a currently active ban.

For each incoming event and relationship type, only the closest valid partner is
selected; ties use the smaller event ID. Reprocessing the same pair creates no
additional finding or notification. This does not enumerate all possible pairs.
Direct ban and GeoBlock facts retain their own source-event identity and confidence.

## Level 2 scan/ban relationships

A `scan_ban_chain` rule groups matching failed or denied requests by its path-pattern
family across multiple IPs, known assets and observed countries. Its anchor is an
observed automatic `security.ban` for a participating IP. Manual bans, simulations
and ASN-policy bans are excluded. This evaluator observes bans; it does not issue them.

For each anchor, OpenSecDash selects that IP's latest matching request at or before
the ban, within the configured follow-up interval (ties use the larger event ID).
It counts matching probes from all IPs in the closed window ending at that request.
Event, distinct-IP, known-asset and known-country thresholds must all hold. Missing,
empty or unknown asset/country values add no distinct asset or country. GeoIP is
changeable descriptive enrichment, not an identity or campaign attribution.

The finding records the ban's event time and IP, without assigning a single asset.
Its retained evidence includes exact counts, the probe window, rule source/version
when known, the follow-up limit and observed gap. Examples contain the ban and up to
19 newest probes, ordered by event time then ID. Confidence is at most `0.8`.
Temporal proximity proves neither the cause of a ban nor a shared campaign,
successful exploitation or a currently active ban.

A separate worker runs about every 60 seconds, processing at most 100 ban anchors per
tick using an ID cursor and a fixed creation-time cutoff. It evaluates at most 20
available chain rules in rule-ID order. All required input producers and any owner
plugin must be enabled. A persisted cursor resumes unfinished cycles after restart;
individual rule/anchor failures are isolated and counted in diagnostics.

Each new cycle rechecks bans **created in the previous 15 minutes**, including newly
imported historical bans. Late requests and GeoIP enrichment can therefore produce a
finding in a later cycle. Unfinished cycles retain their original window across ticks.
Data arriving after a ban leaves that overlap is not automatically correlated against
the full history. There is no startup sweep of old bans or reconstruction of deleted
raw events.

Each rule/ban pair creates at most one finding and eligible opt-in notification.
An additional rule-wide event-time cooldown suppresses other bans in the same window;
those suppressed bans are not included as evidence. Later GeoIP changes do not rewrite
a successful finding or send another email. Historical bans still observe the usual
15-minute event-time notification protection.

The conservative WordPress example uses 20 failures/denies, at least 2 IPs, 2 known
assets and 2 observed countries within 5 minutes, followed by a ban within 60 seconds.
Its stable type is `web.distributed_scan_then_ban`. This payload remains a test fixture;
it is not active in the bundled or public ruleset until the compatible-release rollout
is approved. Validated plugin chain rules can use the implemented evaluator.

## Observed count trends

Two fixed local findings compare **observed** `access.error` counts per known asset
(`asset.access_errors_increased`) and `security.geoblock` counts per known country
(`geo.geoblocks_increased`). Other country events and general country rollups are not
GeoBlock counts. Missing asset or country assignments do not form an unknown series.

The engine compares the last seven completed UTC days with the seven before them.
For example, an evaluation on October 24 compares `[October 17, October 24)` against
`[October 10, October 17)`. The current incomplete day is excluded. A finding requires
all three conditions: at least 50 previous events, at least twice that count now,
and at least 50 additional events. Thus 50 → 100 qualifies; 1 → 20, 0 → 100 and
100 → 149 do not. This is a fixed comparison, not a learned anomaly detector.

Raw retention must be at least 14 days or unlimited. On first activation, the engine
starts a persisted observation period separately for Access and GeoBlock. Changes to
relevant enabled plugin/source IDs restart that metric's wait for 14 full UTC days;
restarts preserve it. Currently failing sources skip evaluation. Diagnostics reports
insufficient data coverage and worker progress. Retention is never extended.

Even after waiting, OpenSecDash cannot prove continuous historical log coverage.
Import gaps or changed logging may explain the increase. A trend does not establish
an attack or trigger a ban. Findings retain exact windows, counts, difference,
factor, dimension and `coverage="observed_only"`, with at most 20 newest example IDs.
This summary survives deletion of raw events. Asset insights and the filtered Events
view show exact half-open intervals in the configured UI timezone. The filtered view
shows at most 20 trend summaries. Dashboard today continues to use the UI's local day.

An hourly worker evaluates at most 100 groups per tick and resumes a persisted daily
cutoff across ticks and restarts. Each dimension creates at most one finding per
cutoff date; late imports never silently rewrite stored comparisons. New notification
rules are disabled, require the matching Access/GeoBlock producer, and preserve the
usual 15-minute event-time protection against delayed email bursts.

## Remote rule updates

The Insights engine ships with a local fallback ruleset, imports active rules into the local database, and also checks for updated heuristic rules from:

```text
https://opensecdash.app/rules/insights-rules.json
```

Before importing that file, the app downloads the fixed `/rules/insights-rules-v1.sha256.json` manifest, checks its expiry and expected path, and verifies the exact ruleset bytes with SHA-256. Responses are bounded to 8 KiB for the manifest and 256 KiB for the ruleset. A failure leaves the last valid database rules active.

This is an explicitly temporary authenticity layer expiring on 2027-04-30 (renewed on 2026-10-24 for six months). It detects accidental or partial publication changes, but because the manifest and rules are served by the same HTTPS website, a compromise of that site or its TLS delivery could replace both. A later release must replace this with an offline-key signature or renew the exception with a new documented scope and expiry.

The source URL is intentionally hardcoded and not configurable in the UI. This keeps the feature predictable and avoids turning the app into a generic remote-code/rule loader.

OpenSecDash refreshes the rules:

- once during app startup, if the cached copy is older than 24 hours
- then about once per day while the app is running

If the remote fetch fails, OpenSecDash continues using the rules already imported into the local database or the bundled fallback ruleset.

Rules are deduplicated by `rule_id`. A remote rule with the same `rule_id` replaces the bundled version, while bundled-only rules remain active. That remote override survives startup seeding, cache hits, HTTP 304, and failed refreshes. A successfully validated replacement restores the bundled fallback for removed remote IDs; remote-only IDs are deactivated. An empty remote ruleset removes all remote overrides in the same way. Plugin rules remain separate and cannot take over IDs owned by built-in rules or another source.

Every import validates the entire ruleset before writing and applies it atomically. Invalid field types, duplicate IDs, empty path patterns, invalid levels, and non-finite or out-of-range confidence values reject the whole import. Confidence `0` and `1` are both valid. Failed imports preserve active rule contents and the last successful remote fetch metadata. Existing notification choices and cooldowns are preserved.

## Privacy

The rules update request downloads a static JSON file. OpenSecDash does not upload local events, IPs, hostnames, settings, or telemetry to fetch insight rules.

## Current heuristic examples

The default ruleset includes patterns for common web probes, for example:

- repeated requests to WordPress endpoints `/wp-login.php` and `/xmlrpc.php`
- phpMyAdmin paths such as `/phpmyadmin` and `/pma/`
- environment files such as `/.env`
- exposed Git repository paths such as `/.git/config`
- repeated probes of specific admin paths such as `/manager/html` and `/boaform/admin/formLogin`
- PHPUnit's `eval-stdin.php` script
- PHP diagnostic pages such as `/phpinfo.php`
- credentials and password-hash files such as `/.aws/credentials`, `/.docker/config.json`, `/.npmrc`, `/.pypirc`, `/.htpasswd`, and `/.netrc`, plus Kubernetes access configuration at `/.kube/config`
- operating system account and password-hash files at `/etc/passwd` and `/etc/shadow`
- PHP configuration backups such as `/wp-config.php.bak` and `/config.php.old`
- potentially sensitive Spring Boot Actuator endpoints such as `/actuator/env`, `/actuator/heapdump`, `/actuator/configprops`, `/actuator/logfile`, and `/actuator/threaddump`
- SSH files under `/.ssh/`, including keys and configuration files
- Subversion and Mercurial metadata under `/.svn/` and `/.hg/`

Generic application paths such as `/config.json`, `/config.php`, `/settings.php`, `/login`,
`/admin`, and `/administrator` are deliberately excluded from the default probe rules:
failed sign-ins, denied access, and application errors on those paths do not by themselves
indicate scanning. Configuration backups and Docker credentials remain covered by their
specific rules, without also generating an environment-file insight for the same request.

The WordPress scan rule requires at least 3 matching denied or failed requests from the
same IP within 5 minutes. The general `/wp-admin` prefix and paths under `/wp-content/`
and `/wp-includes/` are excluded from both WordPress rules: errors while using the admin
interface or loading static files do not by themselves indicate scanning. A single failed
login request does not create a scan insight. Repeated legitimate sign-in failures can
still match; the insight reports a possible scan and needs context from the related events.

System-file and Kubernetes configuration probes can match a single denied or failed
request. These findings identify possible file probes; they do not establish directory
traversal, file disclosure, or access to a Kubernetes cluster.

These rules match denied or failed requests (`access.denied` and `access.error`). They
report possible probes, not confirmed vulnerabilities, exposed credentials, or successful
exploitation. Successful HTTP responses are not covered by these rules. Matching uses
case-insensitive path substrings without URL decoding; encoded variants may be missed.

### PHP detection limits

The PHPUnit and diagnostic page rules do not detect the PHP runtime vulnerabilities
[CVE-2026-109618](https://github.com/php/php-src/security/advisories/GHSA-w8hw-w9fx-j79w)
and [CVE-2026-109619](https://github.com/php/php-src/security/advisories/GHSA-6wmf-4rv5-p3rp).
Those advisories describe memory and request-state handling issues, not a unique request
path. A match on a PHP filename cannot establish whether either vulnerability was attempted
or exploited. Reliable additional indicators would require suitable upstream detection
signals or PHP runtime logs; the current path-only rules do not inspect request bodies or
PHP worker state.

The PHPUnit probe relates to the older `eval-stdin.php` attack surface described by the
[PHPUnit author](https://phpunit.expert/articles/phpunit-a-security-risk.html). PHP diagnostic
output and Spring's potentially sensitive endpoints are documented in the
[PHP manual](https://www.php.net/manual/en/function.phpinfo.php) and
[Spring Boot reference](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html).

## Pattern rules and cooldowns

Rules can use either grouping mode:

- `group_by: "ip"` counts matching requests from the triggering IP.
- `group_by: "path"` counts matching requests for the same path pattern across IPs.

`min_distinct_ips` sets how many different IPs must match. It defaults to `1` and accepts values from `1` to `1000`. For example, the bundled scanner-wave rule requires 20 requests to common WordPress paths from at least 5 IPs in 5 minutes.

After a rule creates an insight, OpenSecDash applies a cooldown for that rule and correlation key for the rule window. This uses event time in the closed interval from the window start to the evaluated event time. Importing a newer wave first does not suppress a separate older wave. IP rules use a canonical IP key; path groups remain global across matching path patterns and assets. Raw events remain unchanged.

New insights store event time separately from creation time and retain a small evidence summary. Rule findings include rule identity/source/version when known, the evaluated time window, exact counts, and at most 20 example event IDs. The summary remains after raw-event retention; example events may no longer be available. Existing insights keep their text and have unknown event time/evidence rather than reconstructed data.

Direct ban and deny findings deduplicate by source event, so separate actual events remain separate facts. GeoIP country enrichment can update a GeoBlock description without creating another finding or email. Insight emails use event time; events older than 15 minutes do not notify.

## Diagnostics and debug reports

Diagnostics shows **Insights engine** as a core plugin, including whether remote rules were loaded or whether OpenSecDash is using database-imported/default rules.

Debug reports include `insight-rules.txt` with:

- hardcoded source URL
- stored rules count
- schema version
- ruleset version
- rule count
- last fetch time
- active rule IDs/titles and their source (`bundled` or `remote`)

Plugin-provided rules use a `plugin:<plugin_id>` source and appear in the same diagnostic summary after validation.

## Rule format

Rules are JSON data with schema versioning. Apps only accept supported schema versions and validate rule size and fields before activating a downloaded ruleset. Developer-facing field documentation lives in [Contributing insight rules](../contributing/insight-rules.md).

`ruleset_version` is a release/date identifier and should use ISO date format:

```text
yyyy-mm-dd
```

`schema_version` describes compatibility:

- `1`, `1.1`, `1.2`, ... are compatible updates for apps that support schema major version `1`.
- `2`, `2.1`, ... are breaking schema changes and require a newer OpenSecDash app version before the ruleset is accepted.

If OpenSecDash sees an unsupported schema major version, it ignores that remote ruleset and keeps using database-imported or bundled rules.

## Investigating findings

IP and Asset Explorer findings link to an evidence detail page. Severity describes
impact; certainty describes evidence strength independently. Direct evidence with
confidence of at least 0.8 is labelled certain, other known evidence at that threshold
probable, and remaining or legacy evidence possible. These are fixed heuristics,
not calibrated probabilities. An observed ban does not establish a currently active ban.

Details retain the rule source/version when known, exact counts and evaluation windows,
event time and creation time. At most 20 stored example event IDs link to Events and
their existing IP/Asset views. Total matches and available examples are separate:
retention may remove some or all examples without invalidating the stored summary.
Type drill-downs use samples from at most 200 findings and show details for at most 20;
additional time filters can further narrow the displayed examples.

Top Insights and finding order use event time, with a labelled creation-time fallback
for legacy records. Asset Explorer's **Most frequent finding** uses the displayed assets
(or focused app) over the last 24 hours. This is not a proven cause. Multi-asset chains
remain accessible through Top Insights, details and examples; a chain's IP is the banned
participant, and its empty asset association represents multiple assets. The examples
do not promise complete coverage of participating IPs/assets or an asset's history.
