<script setup lang="ts">
const providers = [
  'Claude Code',
  'Codex',
  'Cursor',
  'GitHub Copilot',
  'OpenCode',
  'Antigravity',
  'Grok',
  'OpenAI API',
  'Anthropic API',
]
</script>

<template>
  <section class="marquee-band" aria-labelledby="providers-title">
    <p id="providers-title" class="label">Bring the agents you already pay for</p>
    <div class="marquee">
      <ul class="track">
        <li v-for="name in providers" :key="name">{{ name }}</li>
      </ul>
      <ul class="track" aria-hidden="true">
        <li v-for="name in providers" :key="name">{{ name }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.marquee-band {
  position: relative;
  padding: 34px 0 40px;
  border-top: 1px solid var(--rc-line);
  border-bottom: 1px solid var(--rc-line);
  background: linear-gradient(180deg, rgba(18, 16, 45, 0.5), rgba(5, 4, 13, 0.4));
}

.label {
  margin: 0 0 22px;
  text-align: center;
  font-family: var(--rc-font-pixel);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #8d88ad;
}

.marquee {
  display: flex;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
}

.track {
  display: flex;
  flex: none;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  animation: scroll 42s linear infinite;
}

.marquee:hover .track {
  animation-play-state: paused;
}

li {
  display: flex;
  align-items: center;
  gap: 56px;
  padding-right: 56px;
  font-family: var(--rc-font-display);
  font-size: clamp(1.4rem, 2.6vw, 2rem);
  font-weight: 600;
  letter-spacing: -0.02em;
  white-space: nowrap;
  color: rgba(253, 242, 220, 0.62);
  transition: color 0.3s ease;
}

li::after {
  content: '';
  width: 8px;
  height: 8px;
  background: var(--rc-indigo-bright);
  box-shadow: 0 0 14px var(--rc-indigo-bright);
}

li:hover {
  color: var(--rc-ivory);
}

@keyframes scroll {
  to {
    transform: translateX(-100%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .marquee {
    justify-content: center;
    -webkit-mask-image: none;
    mask-image: none;
  }

  .track {
    flex-wrap: wrap;
    justify-content: center;
    row-gap: 12px;
    flex: 1;
    animation: none;
  }

  .track[aria-hidden='true'] {
    display: none;
  }
}
</style>
