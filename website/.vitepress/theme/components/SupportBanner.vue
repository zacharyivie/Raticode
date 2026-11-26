<script setup lang="ts">
import { computed } from 'vue'
import { supportProjectUrl } from '../../../data/site'

// - full: the landing page's strip, after its Download section.
// - aside: docs pages, in the right-hand column under "On this page" (theme/index.ts).
// - compact: the 404 page, and the end of a docs page on screens too narrow for that column.
const props = withDefaults(defineProps<{ variant?: 'full' | 'aside' | 'compact' }>(), { variant: 'full' })

// A docs page holds the aside and the end-of-page banner, one of them hidden, so each needs
// its own heading id.
const titleId = computed(() => (props.variant === 'full' ? 'support-banner-title' : `support-banner-${props.variant}-title`))
</script>

<template>
  <aside
    class="support-banner"
    :class="`is-${variant}`"
    :aria-labelledby="titleId"
    :data-support-banner="variant"
  >
    <svg class="cup" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M8 18h26v10a12 12 0 0 1-12 12h-2A12 12 0 0 1 8 28z" />
      <path d="M34 21h3a5 5 0 0 1 0 10h-3.5" />
      <path class="steam" d="M16 6c-2 2.5 2 4.5 0 7M23 5c-2 2.5 2 4.5 0 7M30 6c-2 2.5 2 4.5 0 7" />
    </svg>
    <div class="copy">
      <p :id="titleId" class="title">Support Raticode</p>
      <p class="text">
        Raticode is free, open source and built by one developer.
        <span v-if="variant === 'full'">If it saves you time, a coffee helps keep new releases coming.</span>
      </p>
    </div>
    <a class="cta" :href="supportProjectUrl" rel="noopener">
      Buy me a coffee
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8" /></svg>
    </a>
  </aside>
</template>

<style scoped>
.support-banner {
  --support-accent: var(--rc-amber, #ffb648);
  display: flex;
  align-items: center;
  gap: clamp(16px, 2.4vw, 28px);
  padding: clamp(20px, 3vw, 28px) clamp(20px, 3.4vw, 36px);
  border: 1px solid rgba(255, 182, 72, 0.32);
  border-radius: 22px;
  background:
    radial-gradient(520px 200px at 0% 0%, rgba(255, 182, 72, 0.16), transparent 70%),
    linear-gradient(120deg, rgba(99, 83, 213, 0.22), rgba(10, 9, 32, 0.6));
  color: var(--rc-ivory, #fdf2dc);
}

.cup {
  flex: none;
  width: 48px;
  height: 48px;
  color: var(--support-accent);
}

.copy {
  flex: 1;
  min-width: 0;
}

.support-banner .title {
  margin: 0;
  font-family: var(--rc-font-display);
  font-size: clamp(1.2rem, 2vw, 1.45rem);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.3;
}

.support-banner .text {
  margin: 4px 0 0;
  font-size: 1rem;
  line-height: 1.55;
  color: var(--rc-mist, #a9a4c8);
}

.support-banner .cta {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 0 20px;
  border-radius: 999px;
  background: var(--support-accent);
  color: #1a1206;
  font-weight: 700;
  font-size: 15px;
  text-decoration: none;
  white-space: nowrap;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.support-banner .cta svg {
  width: 16px;
  height: 16px;
}

.support-banner .cta:hover {
  color: #1a1206;
  transform: translateY(-1px);
  box-shadow: 0 8px 26px rgba(255, 182, 72, 0.35);
}

.support-banner .cta:focus-visible {
  outline: 2px solid var(--rc-ivory, #fdf2dc);
  outline-offset: 3px;
}

/* The docs variants follow the docs theme, light or dark, with a warm tint. */
.is-compact,
.is-aside {
  border-color: rgba(255, 182, 72, 0.5);
  background: linear-gradient(110deg, rgba(255, 182, 72, 0.14), var(--vp-c-bg-soft) 55%);
  color: var(--vp-c-text-1);
}

.is-compact {
  gap: 14px;
  margin: 28px 0 0;
  padding: 14px 18px;
  border-radius: 12px;
}

.is-compact .cup,
.is-aside .cup {
  width: 30px;
  height: 30px;
  color: #b86e00;
}

.dark .is-compact .cup,
.dark .is-aside .cup {
  color: var(--support-accent);
}

.support-banner.is-compact .title,
.support-banner.is-aside .title {
  font-size: 1rem;
}

.support-banner.is-compact .text,
.support-banner.is-aside .text {
  margin-top: 0;
  font-size: 0.92rem;
  color: var(--vp-c-text-2);
}

.support-banner.is-compact .cta:focus-visible,
.support-banner.is-aside .cta:focus-visible {
  outline-color: var(--vp-c-brand-1);
}

.support-banner.is-compact .cta {
  min-height: 40px;
  padding: 0 16px;
  font-size: 14px;
}

/* Under "On this page", 224px wide: the cup and title on one line, the text under them,
   then a full-width button. */
.is-aside {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 10px 10px;
  margin-top: 24px;
  padding: 14px;
  border-radius: 12px;
}

.is-aside .copy {
  display: contents;
}

.is-aside .cup {
  width: 26px;
  height: 26px;
}

.support-banner.is-aside .text {
  grid-column: 1 / -1;
  font-size: 13px;
  line-height: 1.5;
}

.support-banner.is-aside .cta {
  grid-column: 1 / -1;
  justify-content: center;
  min-height: 36px;
  padding: 0 14px;
  font-size: 13px;
}

@media (max-width: 640px) {
  .support-banner:not(.is-aside) {
    flex-wrap: wrap;
  }

  .support-banner:not(.is-aside) .copy {
    flex-basis: calc(100% - 80px);
  }

  .support-banner .cta {
    width: 100%;
    justify-content: center;
  }
}

@media (prefers-reduced-motion: reduce) {
  .support-banner .cta {
    transition: none;
  }

  .support-banner .cta:hover {
    transform: none;
  }
}
</style>
