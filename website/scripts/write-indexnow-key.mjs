import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const key = (process.env.INDEXNOW_KEY || '').trim()

if (!key) {
  console.log('IndexNow key not set; skipping key file generation.')
  process.exit(0)
}

// The IndexNow spec allows a-z, A-Z, 0-9 and dash only - notably NO
// underscore. A key outside this set gets rejected by the search engines
// with a misleading "UserForbiddedToAccessSite", so fail loudly here
// instead.
if (!/^[A-Za-z0-9-]{8,128}$/.test(key)) {
  throw new Error('INDEXNOW_KEY must be 8-128 characters and contain only letters, numbers, or hyphen (the IndexNow spec does not allow underscores).')
}

const publicDir = join(process.cwd(), 'public')
await mkdir(publicDir, { recursive: true })
await writeFile(join(publicDir, `${key}.txt`), `${key}\n`, 'utf8')
console.log(`IndexNow key file prepared: public/${key}.txt`)
