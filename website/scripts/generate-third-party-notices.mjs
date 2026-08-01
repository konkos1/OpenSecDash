import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const websiteDirectory = path.resolve(scriptDirectory, '..')
const lockPath = path.join(websiteDirectory, 'package-lock.json')
const outputPath = path.join(websiteDirectory, 'public', 'third-party-notices.txt')
const checkOnly = process.argv.includes('--check')
const allowedLicenses = new Set(['BSD-2-Clause', 'BSD-3-Clause', 'CC0-1.0', 'ISC', 'MIT'])
const evidencePattern = /^(license|licence|copying|notice|copyright)([._-].*)?$/i

function normalizeEvidence(value) {
  return value
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map(line => line.trimEnd())
    .join('\n')
    .trim()
}

function readPackageJson(packagePath) {
  const filePath = path.join(packagePath, 'package.json')
  return fs.existsSync(filePath)
    ? JSON.parse(fs.readFileSync(filePath, 'utf8'))
    : {}
}

function fallbackEvidence(lockKey, licenseExpression) {
  if (lockKey.startsWith('node_modules/@algolia/') || lockKey.startsWith('node_modules/@docsearch/')) {
    return [{
      name: 'Algolia MIT license',
      text: normalizeEvidence(fs.readFileSync(path.join(websiteDirectory, 'node_modules', '@algolia', 'abtesting', 'LICENSE'), 'utf8'))
    }]
  }
  if (lockKey === 'node_modules/@iconify-json/simple-icons' && licenseExpression === 'CC0-1.0') {
    return [{
      name: 'CC0-1.0 legal-code reference',
      text: [
        'The Simple Icons data package declares CC0-1.0.',
        'The complete CC0 1.0 Universal legal code is available at:',
        'https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt'
      ].join('\n')
    }]
  }
  return []
}

function licenseEvidence(lockKey, packagePath, licenseExpression) {
  const evidence = fs.readdirSync(packagePath, { withFileTypes: true })
    .filter(entry => entry.isFile() && evidencePattern.test(entry.name))
    .map(entry => ({
      name: entry.name,
      text: normalizeEvidence(fs.readFileSync(path.join(packagePath, entry.name), 'utf8'))
    }))
    .filter(entry => entry.text)
  return evidence.length ? evidence : fallbackEvidence(lockKey, licenseExpression)
}

function packageSource(packageJson, lockEntry) {
  if (typeof packageJson.homepage === 'string') return packageJson.homepage
  if (typeof packageJson.repository === 'string') return packageJson.repository
  if (packageJson.repository && typeof packageJson.repository.url === 'string') {
    return packageJson.repository.url
  }
  return lockEntry.resolved || ''
}

function generateNotice() {
  const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'))
  const components = []
  for (const [lockKey, lockEntry] of Object.entries(lock.packages)) {
    if (!lockKey.startsWith('node_modules/') || lockEntry.optional) continue
    const packagePath = path.join(websiteDirectory, lockKey)
    if (!fs.existsSync(packagePath)) {
      throw new Error(`Installed package is missing: ${lockKey}`)
    }
    const packageJson = readPackageJson(packagePath)
    const licenseExpression = lockEntry.license || packageJson.license
    if (!licenseExpression || !allowedLicenses.has(licenseExpression)) {
      throw new Error(`${lockKey} has a missing or unreviewed license: ${licenseExpression || 'UNKNOWN'}`)
    }
    const evidence = licenseEvidence(lockKey, packagePath, licenseExpression)
    if (!evidence.length) {
      throw new Error(`${lockKey} ${lockEntry.version} has no readable license evidence`)
    }
    if (!lockEntry.resolved) {
      throw new Error(`${lockKey} ${lockEntry.version} has no locked source archive`)
    }
    components.push({
      name: lockKey.replace(/^node_modules\//, ''),
      version: lockEntry.version,
      license: licenseExpression,
      homepage: packageSource(packageJson, lockEntry),
      sourceArchive: lockEntry.resolved,
      evidence
    })
  }
  components.sort((left, right) => left.name.localeCompare(right.name) || left.version.localeCompare(right.version))

  const lines = [
    'OpenSecDash website third-party notices',
    '========================================',
    '',
    'This generated file covers packages in the locked VitePress dependency',
    'closure that can contribute code or content to the deployed static website.',
    'Optional platform-specific build binaries are excluded because they are not',
    'delivered to website visitors.',
    '',
    'Package inventory',
    '-----------------',
    ''
  ]
  for (const component of components) {
    lines.push(`${component.name} ${component.version}`)
    lines.push(`  License: ${component.license}`)
    lines.push(`  Project: ${component.homepage}`)
    lines.push(`  Exact source archive: ${component.sourceArchive}`)
    lines.push('')
  }

  const evidenceGroups = new Map()
  for (const component of components) {
    for (const evidence of component.evidence) {
      const digest = crypto.createHash('sha256').update(evidence.text).digest('hex')
      if (!evidenceGroups.has(digest)) {
        evidenceGroups.set(digest, { names: [], documents: [], text: evidence.text })
      }
      const group = evidenceGroups.get(digest)
      group.names.push(`${component.name} ${component.version}`)
      group.documents.push(evidence.name)
    }
  }
  lines.push('License and notice texts')
  lines.push('------------------------')
  lines.push('')
  for (const group of evidenceGroups.values()) {
    lines.push(`Applies to: ${[...new Set(group.names)].join(', ')}`)
    lines.push(`Evidence: ${[...new Set(group.documents)].join(', ')}`)
    lines.push('')
    lines.push(group.text)
    lines.push('')
    lines.push('------------------------------------------------------------------------')
    lines.push('')
  }
  return `${lines.join('\n').trimEnd()}\n`
}

const expected = generateNotice()
const actual = fs.existsSync(outputPath) ? fs.readFileSync(outputPath, 'utf8') : ''
if (actual !== expected) {
  if (checkOnly) {
    console.error('Website third-party notices are stale; run npm run licenses:generate')
    process.exit(1)
  }
  fs.mkdirSync(path.dirname(outputPath), { recursive: true })
  fs.writeFileSync(outputPath, expected)
}
