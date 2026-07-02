import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const key = (process.env.INDEXNOW_KEY || '').trim()

if (!key) {
  console.log('IndexNow key not set; skipping key file generation.')
  process.exit(0)
}

if (!/^[A-Za-z0-9_-]{8,128}$/.test(key)) {
  throw new Error('INDEXNOW_KEY must be 8-128 characters and contain only letters, numbers, underscore, or hyphen.')
}

const publicDir = join(process.cwd(), 'public')
await mkdir(publicDir, { recursive: true })
await writeFile(join(publicDir, `${key}.txt`), `${key}\n`, 'utf8')
console.log(`IndexNow key file prepared: public/${key}.txt`)
