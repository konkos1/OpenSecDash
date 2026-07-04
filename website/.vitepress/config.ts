import { defineConfig } from 'vitepress'

const hostname = 'https://opensecdash.app'

function canonicalUrl(relativePath: string) {
  const cleanPath = relativePath
    .replace(/(^|\/)index\.md$/, '$1')
    .replace(/\.md$/, '')
    .replace(/\/$/, '')

  return cleanPath ? `${hostname}/${cleanPath}` : `${hostname}/`
}

export default defineConfig({
  title: 'OpenSecDash',
  description: 'A security dashboard for homelabs',
  cleanUrls: true,
  lastUpdated: true,
  sitemap: {
    hostname
  },
  head: [
    ['link', { rel: 'icon', href: '/favicon.svg' }],
    ['meta', { name: 'robots', content: 'index,follow' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'OpenSecDash' }],
    ['meta', { property: 'og:title', content: 'OpenSecDash' }],
    ['meta', { property: 'og:description', content: 'A security dashboard for homelabs' }],
    ['meta', { property: 'og:image', content: `${hostname}/og-image.png` }],
    ['meta', { property: 'og:image:type', content: 'image/png' }],
    ['meta', { property: 'og:image:width', content: '1200' }],
    ['meta', { property: 'og:image:height', content: '630' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: `${hostname}/og-image.png` }]
  ],
  transformHead({ pageData }) {
    const url = canonicalUrl(pageData.relativePath)
    return [
      ['link', { rel: 'canonical', href: url }],
      ['meta', { property: 'og:url', content: url }]
    ]
  },
  themeConfig: {
    logo: '/favicon.svg',
    siteTitle: 'OpenSecDash',
    nav: [
      { text: 'Docs', link: '/guide/getting-started/what-is-opensecdash' },
      { text: 'Install', link: '/guide/installation/docker' },
      { text: 'Plugins', link: '/guide/plugins/' },
      { text: 'Support', link: 'https://www.buymeacoffee.com/konkos1' },
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/konkos1/OpenSecDash' },
      { icon: 'docker', link: 'https://hub.docker.com/r/konkos1/OpenSecDash' }
    ],
    search: {
      provider: 'local'
    },
    sidebar: [
      {
        text: 'Getting started',
        items: [
          { text: 'What is OpenSecDash?', link: '/guide/getting-started/what-is-opensecdash' },
          { text: 'Features', link: '/guide/getting-started/features' },
          { text: 'Quickstart', link: '/guide/getting-started/quickstart' },
          { text: 'Security model', link: '/guide/getting-started/security-model' }
        ]
      },
      {
        text: 'Installation',
        items: [
          { text: 'Docker Compose', link: '/guide/installation/docker' },
          { text: 'Bare metal', link: '/guide/installation/bare-metal' },
          { text: 'Reverse proxy', link: '/guide/installation/reverse-proxy' }
        ]
      },
      {
        text: 'Configuration',
        items: [
          { text: 'Settings', link: '/guide/configuration/settings' },
          { text: 'Logging', link: '/guide/configuration/logging' },
          { text: 'Debug reports', link: '/guide/configuration/debug-reports' }
        ]
      },
      {
        text: 'Plugins',
        items: [
          { text: 'Overview', link: '/guide/plugins/' },
          { text: 'Proxmox Assets', link: '/guide/plugins/proxmox-assets' },
          { text: 'JSON Assets', link: '/guide/plugins/json-assets' },
          { text: 'Asset update checks', link: '/guide/plugins/asset-update-checks' },
          { text: 'MQTT to Home Assistant', link: '/guide/plugins/mqtt' },
          { text: 'CrowdSec', link: '/guide/plugins/crowdsec' },
          { text: 'GeoBlock Log', link: '/guide/plugins/geoblock' },
          { text: 'GeoIP', link: '/guide/plugins/geoip' },
          { text: 'Traefik Access Log', link: '/guide/plugins/traefik' }
        ]
      },
      {
        text: 'Operations',
        items: [
          { text: 'Updating', link: '/guide/operations/updating' },
          { text: 'Dashboard rollups', link: '/guide/operations/dashboard-rollups' },
          { text: 'Insights engine', link: '/guide/operations/insight-rules' },
          { text: 'Troubleshooting', link: '/guide/operations/troubleshooting' }
        ]
      },
      {
        text: 'Contributing',
        items: [
          { text: 'Development', link: '/guide/contributing/development' },
          { text: 'Plugin development', link: '/guide/contributing/plugin-development' },
          { text: 'Contributing insight rules', link: '/guide/contributing/insight-rules' },
          { text: 'Translations', link: '/guide/contributing/translations' },
          { text: 'Project information', link: '/guide/contributing/project' }
        ]
      }
    ],
    footer: {
      message: 'Released under the GNU Affero General Public License v3.0.',
      copyright: 'Copyright © konkos1 & OpenSecDash contributors'
    }
  }
})
