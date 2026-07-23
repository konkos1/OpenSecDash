# Release Checklist

OpenSecDash publishes Docker images from version tags. The Git tag is the release version source of truth; `backend/pyproject.toml` intentionally stays at `0.0.0`.

## Version format

Use semantic version tags prefixed with `v`:

```text
v0.1.0
v0.2.0
v1.0.0
```

The Docker publish and GitHub Release workflows run for tags matching:

```text
v*.*.*
```

## Before tagging

1. Make sure the working tree is clean.
2. Run tests and type checks:

```bash
python3 docker/verify-runtime-parity.py
cd backend
uv lock --check
uv sync --python "$(cat .python-version)" --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion "$(cut -d. -f1,2 .python-version)" app tests ../plugins
.venv/bin/alembic heads
.venv/bin/alembic check
```

3. Check dependencies/security status when appropriate:

```bash
cd backend
uv export --quiet --frozen --no-dev --no-emit-project --output-file /tmp/opensecdash-runtime-requirements.txt
uv run --frozen pip-audit --requirement /tmp/opensecdash-runtime-requirements.txt

cd ../website
npm ci
npm run audit:ci
```

4. Run the scratch system profiles documented in
   `backend/tests/performance/README.md`. Never point them at a development database.
5. Update `README.md` or docs if behavior changed.
6. Confirm Docker builds locally if packaging changed:

```bash
docker build -f docker/Dockerfile -t opensecdash:local .
docker run --rm -p 8765:8000 -v opensecdash-data:/data opensecdash:local
```

Open <http://localhost:8765> and verify `/health`.

The publish workflow repeats the image build twice without a dependency cache,
compares installed package versions, verifies core packages against `uv.lock`, scans
the image for fixable high/critical OS and Python findings, and generates an SPDX SBOM.
Review the uploaded audit, package-list, scan, and SBOM artifacts before announcing the
release. Publication does not proceed when a gate fails.

## Automated release gate

The Docker publish workflow is the authoritative release gate. It runs against the
tagged commit and the exact release-candidate image:

| Gate | Evidence retained by CI |
| --- | --- |
| Unit/integration/security regressions | JUnit report for the complete backend suite |
| Types and migrations | Pyright output, Alembic upgrade/heads/check |
| Frontend and documentation | Tailwind reproducibility check and VitePress build |
| Fresh/Small/Large/Upgrade profiles | JSON p50/p95, size, RSS, readiness, search, migration, and startup reports |
| Locked dependencies | Python/npm audit reports and two compared image package lists |
| Image security | SPDX SBOM and Trivy OS/Python report |
| Runtime container | Hardened named-volume health/ready/static/plugin/shutdown smoke |

Fresh and Small are limited to 1 vCPU/512 MiB. Large and Upgrade are limited to
2 vCPU/1 GiB. Search publication gates are p95 below 250 ms for initial lists, 750 ms
for typical terms, and 1,000 ms for no-match searches; serial and parallel readiness
must remain below 250 ms and must not change the database. Reports are uploaded even
when a later gate fails.

## Time-bounded release risks

Every exception is reviewed before its expiry. An expired exception blocks release
until it is removed or explicitly renewed with fresh evidence.

| Risk | Owner | Priority | Expiry/review |
| --- | --- | --- | --- |
| Remote Insight authenticity uses the release-pinned SHA-256 manifest rather than an offline signing key. | Release maintainer | P1 | 2026-10-31 |
| The Vite development server advisory remains in build-only dependencies; docs servers stay loopback-only. | Website maintainer | P1 | 2026-10-31 |
| JSON Assets cannot completely eliminate DNS rebinding between validation and connect. | Security maintainer | P2 | 2026-10-31 |
| Alpine/HTMX still require the documented CSP `unsafe-eval`/inline-style exceptions. | Frontend maintainer | P2 | 2026-10-31 |
| The container entry point starts as root only to repair volume ownership before dropping privileges. | Container maintainer | P2 | 2026-10-31 |

## Create a release tag

```bash
git tag v0.1.0
git push origin v0.1.0
```

The GitHub Actions Docker publish workflow derives `0.1.0` from the tag, passes it into the image as `OPENSECDASH_VERSION`, and builds/pushes:

```text
konkos1/opensecdash:v0.1.0
konkos1/opensecdash:0.1.0
konkos1/opensecdash:latest
```

The Release workflow publishes a GitHub Release for the same tag. Release notes are generated from pull requests associated with commits between the previous version tag and the new tag, not from a full commit list. Each entry includes the PR number, title, and contributor.

### Insight rules rollout note

If a release adds `group_by: "path"` insight rules, mention that the public remote
ruleset will be updated only after the app release is available. Do not publish those
rules before then: older app versions reject the entire remote ruleset.

Update `website/public/rules/insights-rules-v1.sha256.json` whenever the public ruleset
changes. Verify the digest and expiry before tagging.

### Internal authentication release note

For the release that first includes internal user management, use this note:

> OpenSecDash now offers optional internal sign-in with Viewer, Operator, and Admin
> roles. It remains disabled by default, so existing installations need no changes.
> Enable it in Settings, and use `OSD_AUTH_DISABLED` as an emergency lockout-recovery
> switch. See the authentication guide for setup and recovery steps.

## After publishing

1. Verify the generated GitHub Release notes.
2. Verify the Docker image can be pulled and started:

```bash
docker pull konkos1/opensecdash:v0.1.0
docker run --rm -p 8765:8000 -v opensecdash-data:/data konkos1/opensecdash:v0.1.0
```
