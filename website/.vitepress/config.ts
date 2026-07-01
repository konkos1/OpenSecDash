import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'OpenSecDash',
  description: 'A security dashboard for homelabs',
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ['link', { rel: 'icon', href: '/favicon.svg' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'OpenSecDash' }],
    ['meta', { property: 'og:description', content: 'A security dashboard for homelabs' }]
  ],
  themeConfig: {
    logo: '/favicon.svg',
    siteTitle: 'OpenSecDash',
    nav: [
      { text: 'Docs', link: '/guide/getting-started/what-is-opensecdash' },
      { text: 'Install', link: '/guide/installation/docker' },
      { text: 'Plugins', link: '/guide/plugins/' },
      { text: 'GitHub', link: 'https://github.com/konkos1/OpenSecDash' }
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
          { text: 'CrowdSec', link: '/guide/plugins/crowdsec' },
          { text: 'GeoIP', link: '/guide/plugins/geoip' },
          { text: 'Traefik Access Log', link: '/guide/plugins/traefik' }
        ]
      },
      {
        text: 'Operations',
        items: [
          { text: 'Updating', link: '/guide/operations/updating' },
          { text: 'Troubleshooting', link: '/guide/operations/troubleshooting' }
        ]
      },
      {
        text: 'Contributing',
        items: [
          { text: 'Development', link: '/guide/contributing/development' },
          { text: 'Project information', link: '/guide/contributing/project' }
        ]
      }
    ],
    footer: {
      message: 'Released under the GNU Affero General Public License v3.0.',
      copyright: 'Copyright © OpenSecDash contributors'
    }
  }
})
