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

The bundled ruleset may gain `group_by: "path"` rules before the next app release. Do
not add those rules to the public remote ruleset until that app release is available:
older apps would reject the entire remote ruleset. After release, keep both files in
sync.

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
| `schema_version` | yes | Compatibility version of the JSON format. Apps accepting schema major `1` also accept `1.1`, `1.2`, etc. Schema major `2` is breaking and requires a newer app version. |
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
    "/wp-admin",
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
| `level` | no | Severity-like level, usually `low`, `medium`, `high`, or `critical`. Defaults to `medium`. |
| `confidence` | no | Number from `0.0` to `1.0` describing confidence. Defaults to `0.7`. |
| `event_types` | yes | Event types this rule can match, usually `access.denied` and/or `access.error`. |
| `path_contains_any` | yes | List of case-insensitive substrings matched against the request path. |
| `group_by` | no | `ip` counts matches from the triggering IP; `path` counts the same path pattern across IPs. Defaults to `ip`. |
| `window_minutes` | no | Lookback window for counting matching events. Defaults to `5`. Max supported value is `1440`. |
| `threshold` | no | Minimum number of matching events in the window before an insight is created. Defaults to `1`. Max supported value is `100`. |
| `min_distinct_ips` | no | Minimum number of different IPs among matching events. Defaults to `1`. Supported range is `1` to `1000`. |

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

Rules are imported into the local database and deduplicated by `id` / `rule_id`.

If a remote/public rule has the same `id` as a bundled fallback rule, the remote rule replaces the bundled version. Bundled-only rules stay active.

This means rule IDs should be stable. Do not rename a rule ID unless you intentionally want OpenSecDash to treat it as a different rule.

When a rule fires, the engine applies a cooldown for its `window_minutes` and
correlation key. This prevents a request wave from producing a new insight for every
additional matching event.

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
- maximum 50 path patterns per rule
- maximum 200 characters per path pattern
- `threshold` must be between `1` and `100`
- `window_minutes` must be between `1` and `1440`
- `min_distinct_ips` must be between `1` and `1000`
- `group_by` must be `ip` or `path`

## Testing changes

After editing rules, run:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

Then build the website to verify the public JSON is included:

```bash
cd website
npm run docs:build
```

The built file should exist at:

```text
website/.vitepress/dist/rules/insights-rules.json
```
