<script setup lang="ts">
import { computed } from 'vue'
import { withBase } from 'vitepress'
import MazeBackdrop from './MazeBackdrop.vue'
import HeroScene from './HeroScene.vue'
import { usePlatform } from './usePlatform'
import { assetUrl, desktopPlatforms, desktopRelease, releaseUrl } from '../../../../data/releases'

const { os } = usePlatform()

const detected = computed(() => desktopPlatforms.find((platform) => platform.os === os.value) ?? null)
const primaryHref = computed(() => (detected.value ? assetUrl(detected.value.primary.assetName) : '#download'))
const primaryLabel = computed(() => (detected.value ? `Download for ${detected.value.name}` : 'Download Raticode'))
</script>

<template>
  <header class="hero">
    <MazeBackdrop />
    <div class="aurora" aria-hidden="true"><i /><i /><i /></div>

    <div class="rc-container hero-inner">
      <a class="rc-pill hero-pill" :href="releaseUrl">
        <span class="rc-pill-tag">New</span>
        Raticode {{ desktopRelease.version }} is out
        <span aria-hidden="true" class="arrow">→</span>
      </a>

      <h1 class="hero-title">
        Build the maze.<br />
        Let the agents <em>run&nbsp;it.</em>
      </h1>

      <p class="hero-lede">
        Raticode turns the coding work you keep re-prompting into workflows. Wire shell commands, scripts,
        HTTP calls and AI agents together with real branches, loops and reviews. Design them with Rem, run
        them on your machine, and wake up to finished work.
      </p>

      <div class="hero-actions">
        <a class="rc-btn rc-btn-primary" :href="primaryHref">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12m0 0-5-5m5 5 5-5M5 20h14" /></svg>
          {{ primaryLabel }}
        </a>
        <a class="rc-btn rc-btn-ghost" :href="withBase('/docs/')">
          Read the docs
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
        </a>
      </div>

      <p class="hero-meta">
        <span v-if="detected">v{{ desktopRelease.version }} · {{ detected.arch }} · <a href="#download">other platforms</a></span>
        <span v-else>macOS · Windows · Linux · CLI</span>
        <span class="dot" aria-hidden="true" />
        <span>Open source</span>
        <span class="dot" aria-hidden="true" />
        <span>No account required</span>
        <span class="dot" aria-hidden="true" />
        <span class="soon">Mobile coming soon</span>
      </p>

      <div class="hero-stage">
        <img class="stage-rem rc-pixel" :src="withBase('/brand/rem-cyborg.webp')" alt="" width="550" height="640" />
        <HeroScene />
      </div>
    </div>
  </header>
</template>

<style scoped>
.hero {
  position: relative;
  padding: calc(var(--vp-nav-height, 64px) + clamp(40px, 6vw, 76px)) 0 clamp(60px, 8vw, 100px);
  overflow: hidden;
}

.aurora {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.aurora i {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.55;
  animation: drift 18s ease-in-out infinite alternate;
}

.aurora i:nth-child(1) {
  top: -10%;
  left: 8%;
  width: 46vw;
  height: 46vw;
  background: radial-gradient(circle, rgba(99, 83, 213, 0.8), transparent 65%);
}

.aurora i:nth-child(2) {
  top: 18%;
  right: -6%;
  width: 38vw;
  height: 38vw;
  background: radial-gradient(circle, rgba(79, 240, 255, 0.35), transparent 65%);
  animation-delay: -6s;
}

.aurora i:nth-child(3) {
  top: 52%;
  left: 30%;
  width: 40vw;
  height: 30vw;
  background: radial-gradient(circle, rgba(196, 92, 255, 0.28), transparent 65%);
  animation-delay: -11s;
}

@keyframes drift {
  to {
    transform: translate3d(4vw, 3vw, 0) scale(1.12);
  }
}

.hero-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.hero-pill {
  animation: rise 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) both;
  transition: border-color 0.25s ease, color 0.25s ease;
}

.hero-pill:hover {
  border-color: rgba(79, 240, 255, 0.5);
  color: var(--rc-ivory);
}

.hero-pill .arrow {
  color: var(--rc-cyan);
}

.hero-title {
  margin: 30px 0 0;
  font-family: var(--rc-font-display);
  font-size: clamp(3rem, 9.4vw, 7.4rem);
  font-weight: 800;
  line-height: 0.92;
  letter-spacing: -0.055em;
  color: var(--rc-ivory);
  text-shadow: 0 0 80px rgba(143, 130, 255, 0.35);
  animation: rise 1s 0.08s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.hero-title em {
  font-style: normal;
  background: linear-gradient(92deg, #b9b1ff 0%, #8f82ff 35%, #4ff0ff 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  text-shadow: none;
  filter: drop-shadow(0 0 30px rgba(79, 240, 255, 0.35));
}

.hero-lede {
  max-width: 700px;
  margin: 30px 0 0;
  font-size: clamp(1.06rem, 1.7vw, 1.28rem);
  line-height: 1.6;
  color: var(--rc-mist);
  text-wrap: pretty;
  animation: rise 1s 0.16s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 14px;
  margin-top: 38px;
  animation: rise 1s 0.24s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px 14px;
  margin: 22px 0 0;
  font-size: 14px;
  color: #8d88ad;
  animation: rise 1s 0.3s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.hero-meta a {
  color: var(--rc-periwinkle);
  text-decoration: underline;
  text-decoration-color: rgba(185, 177, 255, 0.4);
  text-underline-offset: 3px;
}

.hero-meta .dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #5d587d;
}

.hero-meta .soon {
  color: var(--rc-amber);
}

.hero-stage {
  position: relative;
  width: 100%;
  margin-top: clamp(72px, 8vw, 104px);
  animation: rise-stage 1.4s 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.hero-stage::before {
  content: '';
  position: absolute;
  inset: 8% 6% -6%;
  border-radius: 50%;
  background: radial-gradient(closest-side, rgba(99, 83, 213, 0.55), transparent);
  filter: blur(40px);
  pointer-events: none;
}

.stage-rem {
  position: absolute;
  z-index: 2;
  left: 3%;
  top: 0;
  width: clamp(76px, 9vw, 116px);
  height: auto;
  transform: translateY(-95%);
  filter: drop-shadow(0 10px 30px rgba(79, 240, 255, 0.25));
  pointer-events: none;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(24px);
  }
}

@keyframes rise-stage {
  from {
    opacity: 0;
    transform: translateY(80px) scale(0.96);
  }
}

@media (max-width: 640px) {
  .hero-title br {
    display: none;
  }

  .hero-actions {
    flex-direction: column;
    align-self: stretch;
  }

  .hero-stage {
    margin-top: 104px;
  }

  .stage-rem {
    left: 4%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .aurora i,
  .hero-pill,
  .hero-title,
  .hero-lede,
  .hero-actions,
  .hero-meta,
  .hero-stage {
    animation: none;
  }
}
</style>
