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

export default {
  extends: DefaultTheme,
  // The default theme's 404 page, replaced with links back into the site.
  Layout: () => h(DefaultTheme.Layout, null, { 'not-found': () => h(NotFoundPage) }),
  enhanceApp({ app }) {
    app.component('ProductLanding', ProductLanding)
    app.component('AppScreenshot', AppScreenshot)
    app.component('MobileDownload', MobileDownload)
  },
} satisfies Theme
