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

- WordPress paths such as `/wp-login.php`, `/wp-admin`, and `/xmlrpc.php`
- phpMyAdmin paths such as `/phpmyadmin` and `/pma/`
- exposed environment/config files such as `/.env`
- exposed Git repository paths such as `/.git/config`
- repeated generic admin panel probes

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
