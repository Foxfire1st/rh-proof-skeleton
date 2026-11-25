import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import './tokens.css'
import VerificationBanner from './components/VerificationBanner.vue'
import ConceptReveal from './components/ConceptReveal.vue'
import ConceptHover from './components/ConceptHover.vue'
import ZoomTabs from './components/ZoomTabs.vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('VerificationBanner', VerificationBanner)
    app.component('ConceptReveal', ConceptReveal)
    app.component('ConceptHover', ConceptHover)
    app.component('ZoomTabs', ZoomTabs)
  },
} satisfies Theme
