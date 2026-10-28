import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'

import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/silkscreen/400.css'
import './styles/tokens.css'
import './styles/landing-shell.css'
import './styles/docs.css'

import ProductLanding from './components/landing/ProductLanding.vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('ProductLanding', ProductLanding)
  },
} satisfies Theme
