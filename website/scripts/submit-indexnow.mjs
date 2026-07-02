import { readFile } from 'node:fs/promises'

const key = (process.env.INDEXNOW_KEY || '').trim()
const host = process.env.INDEXNOW_HOST || 'opensecdash.app'
const sitemapPath = process.env.INDEXNOW_SITEMAP || '.vitepress/dist/sitemap.xml'

if (!key) {
  console.log('IndexNow key not set; skipping submission.')
  process.exit(0)
}

const sitemap = await readFile(sitemapPath, 'utf8')
const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]).filter(Boolean)

if (urls.length === 0) {
  throw new Error(`No URLs found in ${sitemapPath}`)
}

const payload = {
  host,
  key,
  keyLocation: `https://${host}/${key}.txt`,
  urlList: urls
}

const response = await fetch('https://api.indexnow.org/indexnow', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(payload)
})

const body = await response.text()
console.log(`IndexNow response: ${response.status} ${response.statusText}`)
if (body) {
  console.log(body)
}

if (!response.ok && response.status !== 202) {
  throw new Error(`IndexNow submission failed with HTTP ${response.status}`)
}

console.log(`Submitted ${urls.length} URL(s) to IndexNow for ${host}.`)
