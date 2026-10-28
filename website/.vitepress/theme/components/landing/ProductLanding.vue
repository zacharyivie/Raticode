<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import './landing.css'
import LandingHero from './LandingHero.vue'
import ProviderMarquee from './ProviderMarquee.vue'
import ProblemSection from './ProblemSection.vue'
import HowItWorks from './HowItWorks.vue'
import WorkflowShowcase from './WorkflowShowcase.vue'
import FeatureBento from './FeatureBento.vue'
import LocalFirst from './LocalFirst.vue'
import DownloadSection from './DownloadSection.vue'
import FinalCta from './FinalCta.vue'
import LandingFooter from './LandingFooter.vue'

const root = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | undefined

// Sections are visible in the static HTML; reveal animations only arm after hydration.
onMounted(() => {
  const el = root.value
  if (!el || !('IntersectionObserver' in window)) return
  el.classList.add('rc-js')
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        entry.target.classList.add('is-visible')
        observer?.unobserve(entry.target)
      }
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0.12 },
  )
  el.querySelectorAll('.rc-reveal').forEach((node) => observer?.observe(node))
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="root" class="rc-landing">
    <LandingHero />
    <main id="main">
      <ProviderMarquee />
      <ProblemSection />
      <HowItWorks />
      <WorkflowShowcase />
      <FeatureBento />
      <LocalFirst />
      <DownloadSection />
      <FinalCta />
    </main>
    <LandingFooter />
  </div>
</template>
