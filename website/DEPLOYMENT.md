# Website deployment

The website is built and deployed by the GitHub Actions `Website` and `Release` workflows.

## When the site goes live

The website follows releases by default:

| Trigger | Deploys? |
| --- | --- |
| PR merged to main with the `website-deploy` label | Yes, immediately (for typo fixes, SEO, general pages). |
| PR merged to main without the label | No - the changes go live with the next release. |
| Version tag pushed (`v*.*.*`) | Yes, always (the `Release` workflow deploys the site so feature docs ship with the release they describe). |
| Direct push to main touching `website/**` | Yes (deliberate maintainer action). |
| Manual `workflow_dispatch` of the `Website` workflow | Yes. |

Note that main has no partial states: any deploy publishes the full current site, including doc changes from earlier unlabeled merges that were still waiting.

Pull requests always get a validation build (no deploy).

## Secrets

Required GitHub repository secrets:

| Secret | Purpose |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID used by Wrangler. |
| `CLOUDFLARE_API_TOKEN` | API token for deploying the Worker/static assets. |
| `INDEXNOW_KEY` | Optional IndexNow key file and submit key. |

The workflow runs:

```bash
npm ci --no-audit
npm run audit:ci
npm run docs:build
npx --yes wrangler@4.113.0 deploy
npm run indexnow:submit
```

IndexNow submission uses `https://api.indexnow.org/indexnow` by default - the spec's generic endpoint, which forwards submissions to Bing and all other IndexNow partners. If the provider rejects a submission with `403` even though the key file is reachable, the script logs the rejection and does not fail the deployment.
