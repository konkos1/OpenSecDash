# Asset update checks

Asset update checks are a core OpenSecDash feature used by asset source plugins such as JSON Assets and Proxmox Assets.

Asset plugins provide systems/apps and optional release metadata. The core update checker then checks whether a newer release is available.

## When it is active

The **Asset update checks** settings section is visible on the Settings page, but it is greyed out until at least one supported asset source plugin is enabled:

- JSON Assets
- Proxmox Assets

Diagnostics shows **Asset update checks** as active only when an asset source is enabled. If no asset source is enabled, it is shown as disabled because there is nothing to check.

## Current provider: GitHub releases

For GitHub release checks, an asset needs a release URL like:

```text
https://github.com/owner/repo/releases/latest
```

OpenSecDash extracts `owner/repo`, queries GitHub, stores the latest release version, and marks the asset as having an update when the installed version differs from the latest version.

During one update-check run, the same GitHub repository is queried only once even if multiple assets point to it.

## Settings

| Setting | What it does |
| --- | --- |
| GitHub API token | Optional token for GitHub release checks to avoid rate limits. |
| GitHub release check interval seconds | How often GitHub releases are checked automatically. Use `0` to disable automatic checks. |

## Manual checks

The Asset Explorer includes a **Check updates** button. It runs the same update logic immediately, independent of the automatic interval.

## Future providers

The feature is intentionally named **Asset update checks** instead of **GitHub checks** because future providers may cover other ecosystems, for example Docker image tags or application-specific release APIs.
