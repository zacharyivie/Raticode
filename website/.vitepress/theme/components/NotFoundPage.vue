<script setup lang="ts">
import { onMounted } from 'vue'
import { useData, withBase } from 'vitepress'
import { newAddress } from '../oldAddress'

// Shown for any address the site doesn't have. GitHub Pages serves 404.html, which the
// config marks noindex, and VitePress renders this in the browser.
const { site } = useData()

// An old /Raticode/... address moves to the same page on this site, never to another host.
onMounted(() => {
  const target = newAddress(window.location, site.value.base)
  if (target) window.location.replace(target)
})
const links = [
  { text: 'Home', href: '/', detail: 'what Raticode is, and downloads for macOS, Windows, Linux and Android' },
  { text: 'Documentation', href: '/docs/', detail: 'every guide, with search in the top bar' },
  { text: 'Getting started', href: '/docs/getting-started', detail: 'install, connect a provider and run a first workflow' },
  { text: 'Installation', href: '/docs/installation', detail: 'every download and package' },
  { text: 'Support', href: '/docs/support', detail: 'report a broken link or get help' },
]
</script>

<template>
  <div class="rc-not-found">
    <img class="rc-pixel" :src="withBase('/brand/rem-think.webp')" alt="Rem, Raticode's pixel-art rat, thinking" width="120" height="141" />
    <p class="code">404</p>
    <h1>This page isn't in the maze</h1>
    <p class="lede">
      The address may be mistyped, or the page may have moved. Try one of these instead.
    </p>
    <ul>
      <li v-for="link in links" :key="link.href">
        <a :href="withBase(link.href)">{{ link.text }}</a>: {{ link.detail }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.rc-not-found {
  max-width: 640px;
  margin: 0 auto;
  padding: 64px 24px 96px;
}
img {
  display: block;
  width: 120px;
  height: auto;
  margin-bottom: 20px;
}
.code {
  font-family: var(--rc-font-mono);
  color: var(--rc-cyan);
  letter-spacing: 0.2em;
  margin: 0 0 8px;
}
h1 {
  font-family: var(--rc-font-display);
  font-size: clamp(2rem, 5vw, 2.8rem);
  line-height: 1.1;
  font-weight: 800;
  margin: 0 0 16px;
}
.lede {
  color: var(--vp-c-text-2);
  margin: 0 0 24px;
  line-height: 1.6;
}
ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 10px;
  color: var(--vp-c-text-2);
}
a {
  font-weight: 600;
  color: var(--vp-c-brand-1);
}
</style>
