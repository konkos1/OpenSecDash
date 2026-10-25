# Insights engine

The Insights engine is a core OpenSecDash feature. It creates insights from two kinds of logic:

- direct facts from datasources, such as CrowdSec bans or GeoBlock denies
- heuristic web-probe rules, such as WordPress or phpMyAdmin scan patterns

The heuristic rules are declarative JSON data. They are not executable code.

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

Rules are deduplicated by `rule_id`. A remote rule with the same `rule_id` replaces the bundled version, while bundled-only rules remain active.

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

After a rule creates an insight, OpenSecDash applies a cooldown for that rule and correlation key for the rule window. Repeated matching events during that window do not create duplicate insights. Raw events remain unchanged.

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
