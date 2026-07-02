import { readFile } from 'node:fs/promises'

const key = (process.env.INDEXNOW_KEY || '').trim()
const host = process.env.INDEXNOW_HOST || 'opensecdash.app'
const sitemapPath = process.env.INDEXNOW_SITEMAP || '.vitepress/dist/sitemap.xml'
const keyLocation = `https://${host}/${key}.txt`

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

if (!key) {
  console.log('IndexNow key not set; skipping submission.')
  process.exit(0)
}

async function keyFileIsPublished() {
  try {
    const response = await fetch(keyLocation, { cache: 'no-store' })
    if (!response.ok) {
      return false
    }
    return (await response.text()).trim() === key
  } catch {
    return false
  }
}

for (let attempt = 1; attempt <= 12; attempt += 1) {
  if (await keyFileIsPublished()) {
    console.log(`IndexNow key file verified: ${keyLocation}`)
    break
  }
  if (attempt === 12) {
    console.log(`IndexNow key file not reachable yet at ${keyLocation}; skipping submission.`)
    process.exit(0)
  }
  console.log(`IndexNow key file not reachable yet; retrying (${attempt}/12)...`)
  await sleep(5000)
}

const sitemap = await readFile(sitemapPath, 'utf8')
const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]).filter(Boolean)

if (urls.length === 0) {
  throw new Error(`No URLs found in ${sitemapPath}`)
}

const payload = {
  host,
  key,
  keyLocation,
  urlList: urls
}

for (let attempt = 1; attempt <= 3; attempt += 1) {
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

  if (response.ok || response.status === 202) {
    console.log(`Submitted ${urls.length} URL(s) to IndexNow for ${host}.`)
    process.exit(0)
  }

  if (response.status === 403 && attempt < 3) {
    console.log(`IndexNow authorization not ready yet; retrying (${attempt}/3)...`)
    await sleep(10000)
    continue
  }

  throw new Error(`IndexNow submission failed with HTTP ${response.status}`)
}
