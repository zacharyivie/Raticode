<script setup lang="ts">
import { withBase } from 'vitepress'
import { usePlatform } from './usePlatform'
import {
  allReleasesUrl,
  assetUrl,
  cliBinaries,
  desktopPlatforms,
  desktopRelease,
  releaseUrl,
} from '../../../../data/releases'

const { os } = usePlatform()

const monogram = { macos: 'MAC', windows: 'WIN', linux: 'LNX' } as const

const published = new Date(`${desktopRelease.publishedAt}T12:00:00Z`).toLocaleDateString('en-US', {
  month: 'long',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
})
</script>

<template>
  <section id="download" class="rc-section download" aria-labelledby="download-title">
    <div class="beam" aria-hidden="true" />
    <div class="rc-container">
      <div class="rc-section-head is-centered rc-reveal">
        <span class="rc-eyebrow">Download</span>
        <h2 id="download-title" class="rc-h2">Start building <em>tonight.</em></h2>
        <p class="rc-lede">
          Raticode {{ desktopRelease.version }} · released {{ published }} ·
          <a :href="releaseUrl">release notes</a>
        </p>
      </div>

      <div class="desktop">
        <article
          v-for="(platform, index) in desktopPlatforms"
          :key="platform.os"
          class="card rc-reveal"
          :class="{ 'is-match': os === platform.os }"
          :style="{ '--rc-delay': `${index * 90}ms` }"
        >
          <span v-if="os === platform.os" class="match">Your system</span>
          <div class="card-head">
            <span class="mono" aria-hidden="true">{{ monogram[platform.os] }}</span>
            <div>
              <h3>{{ platform.name }}</h3>
              <p class="arch">{{ platform.arch }}</p>
            </div>
          </div>

          <a class="rc-btn get" :class="os === platform.os ? 'rc-btn-primary' : 'rc-btn-ghost'" :href="assetUrl(platform.primary.assetName)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12m0 0-5-5m5 5 5-5M5 20h14" /></svg>
            {{ platform.primary.label }}
            <span class="rc-sr-only">for {{ platform.name }} {{ platform.arch }}</span>
          </a>

          <p v-if="platform.alternates.length" class="alts">
            Also:
            <template v-for="(alt, altIndex) in platform.alternates" :key="alt.assetName">
              <a :href="assetUrl(alt.assetName)" :title="alt.detail">{{ alt.label }}<span class="rc-sr-only"> for {{ platform.name }}</span></a><span v-if="altIndex < platform.alternates.length - 1">·</span>
            </template>
          </p>

          <p class="note">{{ platform.note }}</p>
          <a class="sums" :href="assetUrl(platform.checksums)">SHA-256 checksums</a>
        </article>
      </div>

      <div class="extras">
        <article class="cli rc-reveal">
          <span class="kicker">Standalone CLI</span>
          <h3>Headless boxes welcome</h3>
          <p>The gof binary runs the same workflows with no desktop UI, on servers, CI runners and remote machines.</p>
          <ul>
            <li v-for="bin in cliBinaries" :key="bin.assetName">
              <a :href="assetUrl(bin.assetName)">
                <span>{{ bin.label }}</span>
                <code>{{ bin.assetName }}</code>
              </a>
            </li>
          </ul>
        </article>

        <article class="mobile rc-reveal" style="--rc-delay: 100ms">
          <div class="mobile-copy">
            <span class="soon">Coming soon</span>
            <h3>Raticode Mobile</h3>
            <p>
              Follow your threads and keep Rem moving from your phone while your desktop does the heavy lifting.
              Rem is resting up for launch.
            </p>
            <a class="rc-btn rc-btn-ghost" :href="allReleasesUrl">
              <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.56 9.56 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" /></svg>
              Watch releases on GitHub
            </a>
          </div>
          <div class="phone" aria-hidden="true">
            <div class="phone-screen">
              <span class="phone-notch" />
              <img class="rc-pixel" :src="withBase('/brand/rem-sleep.webp')" alt="" width="640" height="545" loading="lazy" />
              <span class="zzz"><i>z</i><i>z</i><i>z</i></span>
              <span class="phone-bar" />
              <span class="phone-bar short" />
            </div>
          </div>
        </article>
      </div>

      <p class="all rc-reveal">
        Looking for an older build? <a :href="allReleasesUrl">Browse all releases →</a>
      </p>
    </div>
  </section>
</template>

<style scoped>
.download {
  overflow: hidden;
}

.beam {
  position: absolute;
  top: 0;
  left: 50%;
  width: min(1100px, 100vw);
  height: 700px;
  transform: translateX(-50%);
  background: radial-gradient(ellipse 50% 50% at 50% 40%, rgba(79, 240, 255, 0.14), transparent 70%),
    radial-gradient(ellipse 60% 60% at 50% 40%, rgba(99, 83, 213, 0.32), transparent 70%);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 35%, #000 60%, transparent);
  mask-image: linear-gradient(180deg, transparent, #000 35%, #000 60%, transparent);
  pointer-events: none;
}

.rc-lede a {
  color: var(--rc-periwinkle);
  text-decoration: underline;
  text-decoration-color: rgba(185, 177, 255, 0.4);
  text-underline-offset: 3px;
}

.desktop {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 60px;
}

.card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 28px;
  border: 1px solid var(--rc-line);
  border-radius: 22px;
  background: linear-gradient(170deg, rgba(27, 24, 64, 0.82), rgba(9, 8, 28, 0.94));
  transition: border-color 0.4s ease, box-shadow 0.4s ease;
}

.card.is-match {
  border-color: rgba(79, 240, 255, 0.45);
  box-shadow:
    0 0 0 1px rgba(79, 240, 255, 0.15),
    0 30px 80px -30px rgba(79, 240, 255, 0.45);
}

.match {
  position: absolute;
  top: -12px;
  right: 22px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--rc-cyan);
  font-family: var(--rc-font-pixel);
  font-size: 10.5px;
  letter-spacing: 0.06em;
  color: #06202a;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 16px;
}

.mono {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  border: 1px solid rgba(143, 130, 255, 0.45);
  border-radius: 14px;
  background: rgba(99, 83, 213, 0.22);
  font-family: var(--rc-font-pixel);
  font-size: 13px;
  color: var(--rc-ivory);
  box-shadow: inset 0 0 20px rgba(143, 130, 255, 0.25);
}

h3 {
  margin: 0;
  font-family: var(--rc-font-display);
  font-size: 1.6rem;
  font-weight: 700;
  letter-spacing: -0.025em;
  color: var(--rc-ivory);
}

.arch {
  margin: 2px 0 0;
  font-family: var(--rc-font-mono);
  font-size: 13px;
  color: var(--rc-mist);
}

.get {
  width: 100%;
}

.alts {
  display: flex;
  gap: 8px;
  margin: -4px 0 0;
  font-size: 14px;
  color: #8d88ad;
}

.alts a,
.sums {
  color: var(--rc-periwinkle);
  text-decoration: underline;
  text-decoration-color: rgba(185, 177, 255, 0.35);
  text-underline-offset: 3px;
}

.alts a:hover,
.sums:hover {
  color: var(--rc-ivory);
}

.note {
  margin: auto 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--rc-line);
  font-size: 13.5px;
  line-height: 1.55;
  color: #9f9ac2;
}

.sums {
  margin-top: -6px;
  font-size: 13px;
}

.extras {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 18px;
  margin-top: 18px;
}

.cli,
.mobile {
  padding: 30px;
  border: 1px solid var(--rc-line);
  border-radius: 22px;
  background: linear-gradient(170deg, rgba(27, 24, 64, 0.82), rgba(9, 8, 28, 0.94));
}

.kicker {
  font-family: var(--rc-font-pixel);
  font-size: 11px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--rc-cyan);
}

.cli h3,
.mobile h3 {
  margin-top: 12px;
}

.cli p,
.mobile p {
  margin: 10px 0 0;
  font-size: 15.5px;
  line-height: 1.6;
  color: var(--rc-mist);
}

.cli ul {
  margin: 20px 0 0;
  padding: 0;
  list-style: none;
}

.cli li a {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
  border-top: 1px solid var(--rc-line);
  font-size: 14.5px;
  color: var(--rc-ivory-dim);
  transition: color 0.2s ease;
}

.cli li a:hover {
  color: var(--rc-ivory);
}

.cli li a:hover code {
  color: var(--rc-cyan);
}

.cli code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--rc-font-mono);
  font-size: 12.5px;
  color: var(--rc-periwinkle);
  transition: color 0.2s ease;
}

.mobile {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 28px;
  align-items: center;
  overflow: hidden;
  background:
    radial-gradient(500px 300px at 90% 60%, rgba(255, 182, 72, 0.12), transparent 65%),
    linear-gradient(170deg, rgba(27, 24, 64, 0.82), rgba(9, 8, 28, 0.94));
}

.soon {
  display: inline-block;
  padding: 4px 10px;
  border: 1px solid rgba(255, 182, 72, 0.5);
  border-radius: 999px;
  background: rgba(255, 182, 72, 0.1);
  font-family: var(--rc-font-pixel);
  font-size: 10.5px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--rc-amber);
}

.mobile .rc-btn {
  margin-top: 24px;
  min-height: 46px;
  font-size: 14.5px;
}

.phone {
  width: 170px;
  padding: 8px;
  border: 1px solid rgba(196, 188, 255, 0.3);
  border-radius: 32px;
  background: linear-gradient(160deg, #26214f, #0e0c26);
  box-shadow:
    0 30px 60px -20px rgba(0, 0, 0, 0.7),
    0 0 60px -10px rgba(255, 182, 72, 0.25);
  transform: rotate(6deg);
}

.phone-screen {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  height: 300px;
  padding: 70px 16px 20px;
  border-radius: 25px;
  background: radial-gradient(circle at 50% 40%, #1d1947, #07061a 70%);
  overflow: hidden;
}

.phone-notch {
  position: absolute;
  top: 10px;
  width: 54px;
  height: 14px;
  border-radius: 999px;
  background: #05040d;
}

.phone-screen img {
  width: 120px;
  height: auto;
}

.phone-bar {
  width: 80%;
  height: 9px;
  border-radius: 5px;
  background: rgba(185, 177, 255, 0.14);
}

.phone-bar.short {
  width: 52%;
}

.zzz {
  position: absolute;
  top: 46px;
  right: 34px;
  font-family: var(--rc-font-pixel);
  color: var(--rc-amber);
}

.zzz i {
  position: absolute;
  font-style: normal;
  opacity: 0;
  animation: zzz 3s ease-in-out infinite;
}

.zzz i:nth-child(2) {
  animation-delay: 1s;
  font-size: 1.2em;
}

.zzz i:nth-child(3) {
  animation-delay: 2s;
  font-size: 1.45em;
}

@keyframes zzz {
  0% {
    opacity: 0;
    transform: translate(0, 0);
  }
  30% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: translate(16px, -28px);
  }
}

.all {
  margin: 36px 0 0;
  text-align: center;
  font-size: 15px;
  color: #8d88ad;
}

.all a {
  color: var(--rc-periwinkle);
}

.all a:hover {
  color: var(--rc-ivory);
}

@media (max-width: 960px) {
  .desktop,
  .extras {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .mobile {
    grid-template-columns: 1fr;
  }

  .phone {
    justify-self: center;
  }

  .cli li a {
    flex-direction: column;
    gap: 2px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .zzz i {
    animation: none;
    opacity: 1;
    position: static;
  }
}
</style>
