<script setup lang="ts">
import { withBase } from 'vitepress'
import { allReleasesUrl } from '../../../../data/releases'

const repo = 'https://github.com/zacharyivie/Raticode'
</script>

<template>
  <footer class="footer">
    <div class="rc-container inner">
      <div class="brand">
        <img :src="withBase('/brand/roundel-64.png')" alt="" width="32" height="32" />
        <div>
          <strong>Raticode</strong>
          <span>Local agentic workflows for people who ship.</span>
        </div>
      </div>
      <nav aria-label="Footer">
        <a :href="withBase('/docs/')">Docs</a>
        <a href="#download">Download</a>
        <a :href="allReleasesUrl">Changelog</a>
        <a :href="repo">GitHub</a>
        <a :href="`${repo}/issues`">Support</a>
        <a :href="`${repo}/blob/main/LICENSE`">AGPL-3.0</a>
      </nav>
    </div>
    <p class="wordmark" aria-hidden="true">RATICODE</p>
  </footer>
</template>

<style scoped>
.footer {
  position: relative;
  padding: 56px 0 0;
  border-top: 1px solid var(--rc-line);
  overflow: hidden;
}

.inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand div {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}

.brand strong {
  font-family: var(--rc-font-display);
  font-size: 18px;
  color: var(--rc-ivory);
}

.brand span {
  font-size: 14px;
  color: #8d88ad;
}

nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 26px;
  font-size: 15px;
}

nav a {
  color: var(--rc-mist);
  transition: color 0.2s ease;
}

nav a:hover {
  color: var(--rc-ivory);
}

.wordmark {
  margin: 40px 0 0;
  font-family: var(--rc-font-pixel);
  font-size: clamp(3rem, 15.5vw, 13.5rem);
  font-weight: 400;
  line-height: 0.8;
  letter-spacing: -0.02em;
  white-space: nowrap;
  text-align: center;
  color: transparent;
  background: linear-gradient(180deg, rgba(143, 130, 255, 0.3), rgba(143, 130, 255, 0));
  -webkit-background-clip: text;
  background-clip: text;
  user-select: none;
}
</style>
