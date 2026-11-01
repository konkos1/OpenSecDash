# Contributing insight rules

The Insights engine uses a declarative JSON ruleset. Community contributions should extend this ruleset instead of adding Python code for simple web-probe patterns.

Rules live in:

```text
website/public/rules/insights-rules.json
```

The bundled fallback copy lives in:

```text
backend/app/insights/rules/default-rules.json
```

The bundled ruleset may gain `group_by: "path"` rules before the next app release. Do not add those rules to the public remote ruleset until that app release is available: older apps would reject the entire remote ruleset. After release, keep both files in sync.

The public ruleset also has a release-pinned SHA-256 manifest:

```text
website/public/rules/insights-rules-v1.sha256.json
```

After every public ruleset change, update the manifest digest with `shasum -a 256 website/public/rules/insights-rules.json`, set a reviewed future expiry date, and test both files together. OpenSecDash rejects a missing, expired, or mismatching manifest before importing any remote rules.

## Top-level structure

```json
{
  "schema_version": 1,
  "ruleset_version": "2026-07-11",
  "rules": []
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Compatibility version of the JSON format. Apps accepting schema major `1` also accept `1.1`, `1.2`, etc. This app accepts majors `1` and `2`, including their minor versions. Major `2` requires a compatible app release; older apps reject it. |
| `ruleset_version` | yes | Date/version of this ruleset in `yyyy-mm-dd` format. Update this when changing rules. |
| `rules` | yes | Array of rule objects. |

## Rule object

Example:

```json
{
  "id": "web.scanner_wave",
  "title": "Scanner wave detected",
  "description": "Requests targeting common WordPress paths were observed.",
  "level": "high",
  "confidence": 0.85,
  "event_types": ["access.denied", "access.error"],
  "path_contains_any": [
    "/wp-login.php",
    "/xmlrpc.php"
  ],
  "group_by": "path",
  "window_minutes": 5,
  "threshold": 20,
  "min_distinct_ips": 5
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable unique rule ID. Use a namespace such as `web.wordpress_scan`. This is used for DB deduplication. |
| `title` | yes | Short human-readable title shown in the UI. |
| `description` | no | Longer explanation shown in the UI/debug output. Falls back to `title` if omitted. |
| `level` | no | Severity-like level; only `low`, `medium`, `high`, or `critical`. Defaults to `medium`. |
| `confidence` | no | Number from `0.0` to `1.0` describing confidence. Defaults to `0.7`. |
| `event_types` | yes | Event types this rule can match, usually `access.denied` and/or `access.error`. |
| `path_contains_any` | yes | List of case-insensitive substrings matched against the request path. |
| `group_by` | no | `ip` counts matches from the triggering IP; `path` counts the same path pattern across IPs. Defaults to `ip`. |
| `window_minutes` | no | Lookback window for counting matching events. Defaults to `5`. Max supported value is `1440`. |
| `threshold` | no | Minimum number of matching events in the window before an insight is created. Defaults to `1`. Max supported value is `100`. |
| `min_distinct_ips` | no | Minimum number of different IPs among matching events. Defaults to `1`. Supported range is `1` to `1000`. |

Rule IDs are limited to 100 characters and titles to 255 characters. These limits match the stored Insight and notification metadata and are validated before import.

## Matching behavior

A rule matches when all of these are true:

1. the event has an IP address and path,
2. the event type is listed in `event_types`,
3. the path contains at least one entry from `path_contains_any`,
4. the matching IP or path group has at least `threshold` matching events within `window_minutes`,
5. at least `min_distinct_ips` different IPs match.

Matching is intentionally simple and safe:

- no remote Python code
- no `eval`
- no dynamic imports
- no user-provided rule source URL
- no regular expressions for now

## Deduplication and overrides

Rules are imported atomically into the local database and deduplicated by `id` / `rule_id`. The entire payload is validated before any rule is written; a validation or database failure rolls back the import.

If a remote/public rule has the same `id` as a bundled fallback rule, the remote rule replaces the bundled version. Bundled-only rules stay active. Startup seeding, a cached refresh, HTTP 304, and failed downloads preserve existing remote overrides. Only a fully validated remote replacement can remove remote rules: an omitted rule falls back to the bundled rule with the same ID, or is deactivated if no bundled fallback exists. An empty remote rules list applies the same removal behavior.

Plugin rules cannot replace built-in, bundled, remote, or another plugin's rule IDs. A conflict rejects that plugin's entire import and records a diagnostic warning; other plugins can still load. Conflict-free plugin IDs do not require a namespace. Imports preserve existing notification enabled, window, and cooldown settings.

This means rule IDs should be stable. Do not rename a rule ID unless you intentionally want OpenSecDash to treat it as a different rule.

When a rule fires, the engine applies a cooldown for its `window_minutes` and correlation key. This prevents a request wave from producing a new insight for every additional matching event.

## Choosing good rule IDs

Recommended patterns:

```text
web.wordpress_scan
web.phpmyadmin_probe
web.env_file_probe
web.git_probe
web.admin_probe
```

Guidelines:

- use lowercase
- use dots for namespaces
- keep IDs stable
- prefer specific names over vague ones

## Writing good path patterns

Use clear substrings that are likely to appear in malicious scans and unlikely to create noisy false positives.

Good examples:

```json
"path_contains_any": [
  "/.env",
  "/.git/config",
  "/wp-login.php"
]
```

Be careful with broad patterns such as:

```json
"path_contains_any": ["/login", "/admin"]
```

For broad patterns, use a higher threshold, for example:

```json
"window_minutes": 10,
"threshold": 3
```

## Validation limits

OpenSecDash validates downloaded rules before activating them:

- supported schema major version only
- maximum 200 rules per ruleset
- unique, nonempty rule IDs up to 100 characters; nonempty titles up to 255 characters
- descriptions must be strings up to 2,000 characters; ruleset versions must be strings up to 20 characters
- `level` must be `low`, `medium`, `high`, or `critical`
- `confidence` must be a finite number from `0` through `1`, including both boundaries
- 1 to 50 nonempty event-type strings, each up to 50 characters
- maximum 50 nonempty path patterns per rule
- maximum 200 characters per path pattern
- `threshold` must be between `1` and `100`
- `window_minutes` must be between `1` and `1440`
- `min_distinct_ips` must be between `1` and `1000`
- `group_by` must be `ip` or `path`

Numeric thresholds and windows require integers; booleans and numeric strings are rejected. All declared fields must have their expected types. Whitespace-only IDs, titles, event types, and path patterns are rejected. Additional metadata fields are tolerated but do not affect rule execution.

Schema 1 matching uses case-insensitive substrings without URL decoding or additional path normalization. For `group_by: "path"`, the group covers the rule's complete path-pattern list across IPs, rather than one exact request path.

## Testing changes

After editing rules, run:

```bash
cd backend
uv lock --check
uv sync --python "$(cat .python-version)" --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion "$(cut -d. -f1,2 .python-version)" app tests ../plugins
```

Then build the website to verify the public JSON is included:

```bash
cd website
npm ci
npm run audit:ci
npm run docs:build
```

The built file should exist at:

```text
website/.vitepress/dist/rules/insights-rules.json
website/.vitepress/dist/rules/insights-rules-v1.sha256.json
```

## Schema 2: precise probe matching

Apps supporting schema 2 also read schema 1 with its original substring semantics.
Schema 2 adds `kind` directly on the rule (`web_probe` by default, or
`scan_ban_chain`) and a `match_options` object. Existing fields stay at their
original positions. Unknown keys inside `match_options` are rejected.

For example, this is a local test rule, not an additional bundled probe type:

```json
{
  "schema_version": 2,
  "ruleset_version": "2026-11-01",
  "rules": [{
    "id": "test.exact_probe",
    "title": "Possible diagnostic file probe",
    "kind": "web_probe",
    "event_types": ["access.denied", "access.error"],
    "threshold": 2,
    "match_options": {
      "path_equals_any": ["/phpinfo.php"],
      "status_codes": [403, 404],
      "methods": ["GET", "HEAD"]
    }
  }]
}
```

| Match option | Limits and behavior |
| --- | --- |
| `path_equals_any` | At most 50 nonempty strings of at most 200 characters; matches the entire stored path. |
| `path_prefix_any` | Same limits; matches the beginning of the stored path. |
| `status_codes` | If present, 1–100 distinct integers between 100 and 599. |
| `methods` | If present, 1–10 values from GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, CONNECT, TRACE; stored uppercase. |

At least one of `path_contains_any`, `path_equals_any`, or `path_prefix_any` must
be nonempty. Path lists combine with OR; event type, path, status, and method
combine with AND. A missing status or method option adds no restriction; an empty
status or method list is invalid. A missing event method cannot match a method
filter. Paths remain case-insensitive, `%` and `_` are literal characters, and
there is no URL decoding or additional normalization. Query-like characters are
part of the stored path, so `/phpinfo.php?x=1` differs from `/phpinfo.php` for exact
matching. Methods match case-insensitively.

Counts use SQL aggregates over the closed event-time window. Schema-2 samples
contain at most the newest 20 events by event time, then ID, while evidence keeps
exact total and distinct counts. `group_by: "path"` still groups all matching
paths for the rule across IPs. Importing schema 1 over the same rule ID resets
`kind` to `web_probe` and clears all schema-2 options.

### Scan/ban chain contract

Schema 2 validates and stores `kind: "scan_ban_chain"` using the same probe
fields. A separate background worker evaluates chains; synchronous event ingestion
skips them. See [Level 2 relationships](../operations/insight-rules#level-2-scan-ban-relationships)
for event-time selection, evidence, bounded batches and the 15-minute recheck limit.
No production chain rule is shipped.

Chains require `group_by: "path"`, `min_distinct_ips` of at least 2, a window of
at most 60 minutes, and only `access.denied` and/or `access.error` event types.
The follow-up endpoint is fixed to `security.ban` and cannot be configured.
Additional `match_options` are:

| Option | Default | Range |
| --- | --- | --- |
| `min_distinct_assets` | 2 | 2–100 |
| `min_distinct_countries` | 2 | 1–100 |
| `followup_seconds` | 60 | 1–300 |

A ruleset contains at most 200 rules, including at most 20 chains. Booleans are
not integers. Catalog availability requires an enabled Access producer **and**
an enabled `security.ban` producer, plus the owner plugin when applicable.
Imports and reseeding preserve notification opt-ins, windows, and cooldowns;
new notification rules remain disabled.

### Release and remote rollout

Keep the bundled and public rulesets on schema 1 for now. Publish a compatible
app release before considering schema 2 remotely: older clients reject the new
major, and schema-2 conditions must never be disguised as schema 1 because older
clients could ignore them and match too broadly. Activate chain rules only after
the compatible-release rollout is approved. The fixed HTTPS URLs, manifest verification,
size limits, and last-known-good fallback remain unchanged; no new feed is added.
