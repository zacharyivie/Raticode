import { h } from 'vue'
import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'

import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/silkscreen/400.css'
import './styles/tokens.css'
import './styles/landing-shell.css'
import './styles/docs.css'

import ProductLanding from './components/landing/ProductLanding.vue'
import AppScreenshot from './components/AppScreenshot.vue'
import MobileDownload from './components/MobileDownload.vue'
import NotFoundPage from './components/NotFoundPage.vue'
import SupportBanner from './components/SupportBanner.vue'

export default {
  extends: DefaultTheme,
  // The default theme's 404 page, replaced with links back into the site, and the support
  // banner on docs pages: in the right-hand column under "On this page", and at the end of
  // the page for screens too narrow for that column, where docs.css shows it instead.
  Layout: () => h(DefaultTheme.Layout, null, {
    'not-found': () => h(NotFoundPage),
    'aside-outline-after': () => h(SupportBanner, { variant: 'aside' }),
    'doc-after': () => h('div', { class: 'support-banner-end' }, h(SupportBanner, { variant: 'compact' })),
  }),
  enhanceApp({ app }) {
    app.component('ProductLanding', ProductLanding)
    app.component('AppScreenshot', AppScreenshot)
    app.component('MobileDownload', MobileDownload)
  },
} satisfies Theme
