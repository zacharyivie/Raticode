<script setup lang="ts">
import { withBase } from 'vitepress'
</script>

<template>
  <section class="rc-section final" aria-labelledby="final-title">
    <div class="rc-container">
      <div class="panel rc-reveal">
        <div class="rings" aria-hidden="true"><i /><i /><i /></div>
        <img class="rem rc-pixel" :src="withBase('/brand/rem-wave.webp')" alt="" width="594" height="640" loading="lazy" />
        <h2 id="final-title">Let the agents<br /><em>run the maze.</em></h2>
        <p>Rem is waiting. Your first workflow is one sentence away.</p>
        <div class="actions">
          <a class="rc-btn rc-btn-primary" href="#download">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12m0 0-5-5m5 5 5-5M5 20h14" /></svg>
            Download Raticode
          </a>
          <a class="rc-btn rc-btn-ghost" :href="withBase('/docs/')">Read the docs</a>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.final {
  padding-top: 40px;
}

.panel {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: clamp(56px, 8vw, 96px) clamp(24px, 5vw, 64px);
  border: 1px solid rgba(143, 130, 255, 0.3);
  border-radius: 36px;
  background:
    radial-gradient(800px 400px at 50% 0%, rgba(99, 83, 213, 0.55), transparent 70%),
    radial-gradient(600px 300px at 50% 120%, rgba(79, 240, 255, 0.18), transparent 70%),
    #0a0920;
  text-align: center;
  overflow: hidden;
}

.rings {
  position: absolute;
  top: 0;
  left: 50%;
  pointer-events: none;
}

.rings i {
  position: absolute;
  top: 0;
  left: 0;
  width: 360px;
  height: 360px;
  border: 1px solid rgba(185, 177, 255, 0.16);
  border-radius: 50%;
  transform: translate(-50%, -30%);
  animation: ring 6s ease-out infinite;
}

.rings i:nth-child(2) {
  animation-delay: 2s;
}

.rings i:nth-child(3) {
  animation-delay: 4s;
}

@keyframes ring {
  from {
    opacity: 0.9;
    transform: translate(-50%, -30%) scale(0.6);
  }
  to {
    opacity: 0;
    transform: translate(-50%, -30%) scale(2.6);
  }
}

.rem {
  position: relative;
  width: clamp(110px, 14vw, 150px);
  height: auto;
  filter: drop-shadow(0 12px 40px rgba(143, 130, 255, 0.55));
  animation: bob 3.2s ease-in-out infinite;
}

@keyframes bob {
  50% {
    transform: translateY(-8px);
  }
}

h2 {
  position: relative;
  margin: 28px 0 0;
  font-family: var(--rc-font-display);
  font-size: clamp(2.6rem, 7vw, 5.4rem);
  font-weight: 800;
  line-height: 0.95;
  letter-spacing: -0.05em;
  color: var(--rc-ivory);
}

h2 em {
  font-style: normal;
  background: linear-gradient(92deg, #b9b1ff 0%, #8f82ff 35%, #4ff0ff 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

p {
  position: relative;
  margin: 22px 0 0;
  font-size: clamp(1.05rem, 1.7vw, 1.25rem);
  color: var(--rc-mist);
}

.actions {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 14px;
  margin-top: 36px;
}

@media (prefers-reduced-motion: reduce) {
  .rings i,
  .rem {
    animation: none;
  }
}
</style>
