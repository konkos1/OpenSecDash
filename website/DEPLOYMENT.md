# Deploying opensecdash.app

The website is a VitePress static site.

## Recommended: Cloudflare Pages

If you create a Cloudflare Pages project, use:

- Root directory: `website`
- Build command: `npm ci && npm run docs:build`
- Build output directory: `.vitepress/dist`

Then add custom domains:

- `opensecdash.app`
- `www.opensecdash.app` optional

Cloudflare Pages automatically provisions and renews HTTPS certificates.

## If you created a Cloudflare Worker instead

This repository also includes `website/wrangler.toml` for Cloudflare Workers Static Assets:

```toml
name = "opensecdash-app"
compatibility_date = "2026-06-30"

[assets]
directory = ".vitepress/dist"
not_found_handling = "single-page-application"
```

Build locally or in CI:

```bash
cd website
npm ci
npm run docs:build
npx wrangler deploy
```

For GitHub-based deployment with Wrangler, create Cloudflare API token and account secrets in GitHub, then run the same build/deploy commands in an Actions workflow.

## CI build check

`.github/workflows/website.yml` builds the website on pull requests and pushes that touch `website/**`.
