// @ts-check
// Docusaurus config for the minutes documentation site.
// Run: npm install && npm start  (dev)  |  npm run build  (static build in build/)

import {themes as prismThemes} from 'prism-react-renderer';

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'minutes',
  tagline: 'Self-hosted, EU-friendly live meeting transcription + translation',
  favicon: 'img/favicon.svg',

  url: 'https://gettheminutes.com',
  baseUrl: '/docs/', // hosted under gettheminutes.com/docs (Caddy serves the static build there)

  organizationName: 'arjmandi',
  projectName: 'minutes',

  onBrokenLinks: 'warn',
  markdown: {hooks: {onBrokenMarkdownLinks: 'warn'}},

  i18n: {defaultLocale: 'en', locales: ['en']},

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          routeBasePath: '/', // docs at the site root
          sidebarPath: './sidebars.js',
          editUrl: 'https://github.com/arjmandi/minutes/tree/main/',
        },
        blog: false,
        theme: {customCss: './src/css/custom.css'},
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {defaultMode: 'light', respectPrefersColorScheme: true},
      navbar: {
        title: 'minutes',
        logo: {alt: 'minutes', src: 'img/logo.svg'},
        items: [
          {type: 'docSidebar', sidebarId: 'docs', position: 'left', label: 'Docs'},
          {href: 'https://github.com/arjmandi/minutes', label: 'GitHub', position: 'right'},
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              {label: 'Introduction', to: '/'},
              {label: 'Deploy (admins)', to: '/admin/deploy'},
              {label: 'Using minutes', to: '/users/web-app'},
            ],
          },
          {
            title: 'Project',
            items: [
              {label: 'GitHub', href: 'https://github.com/arjmandi/minutes'},
              {label: 'Soniox (STT)', href: 'https://soniox.com'},
              {label: 'Anthropic (translation)', href: 'https://console.anthropic.com'},
            ],
          },
        ],
        copyright: `minutes — MIT licensed. Built for privacy-conscious teams.`,
      },
      prism: {theme: prismThemes.github, darkTheme: prismThemes.dracula},
    }),
};

export default config;
