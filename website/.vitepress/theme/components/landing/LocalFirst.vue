<script setup lang="ts">
const promises = [
  { title: 'No account. No login.', body: 'Install it and start. There is nothing to sign up for.' },
  { title: 'No Raticode cloud', body: 'Workflows, threads and run history live on your machine. There is no Raticode server in the middle.' },
  { title: 'Secrets stay out of source', body: 'Workflows refer to secrets by name, so keys never land in the files you commit.' },
  { title: 'Your providers, your terms', body: 'Agents run through the subscriptions and API keys you already have, under your own accounts.' },
]
</script>

<template>
  <section class="rc-section local" aria-labelledby="local-title">
    <div class="glow" aria-hidden="true" />
    <div class="rc-container layout">
      <div class="rc-reveal">
        <span class="rc-eyebrow">Local by design</span>
        <h2 id="local-title" class="rc-h2">Your machine.<br />Your keys.<br /><em>Your rules.</em></h2>
        <p class="rc-lede">
          Raticode is a desktop app and an open-source CLI, not a hosted service. Small, focused agent steps also mean
          smaller prompts, so you spend tokens on the work instead of the scrollback.
        </p>
      </div>

      <ul class="list">
        <li v-for="(item, index) in promises" :key="item.title" class="rc-reveal" :style="{ '--rc-delay': `${index * 80}ms` }">
          <span class="check" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10" /></svg>
          </span>
          <div>
            <h3>{{ item.title }}</h3>
            <p>{{ item.body }}</p>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.local {
  overflow: hidden;
}

.glow {
  position: absolute;
  top: 10%;
  left: -10%;
  width: 60vw;
  height: 60vw;
  max-width: 800px;
  max-height: 800px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(99, 83, 213, 0.32), transparent 65%);
  filter: blur(40px);
  pointer-events: none;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: clamp(40px, 7vw, 100px);
  align-items: center;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.list li {
  display: flex;
  gap: 18px;
  padding: 22px 24px;
  border: 1px solid var(--rc-line);
  border-radius: 18px;
  background: rgba(14, 12, 38, 0.7);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
}

.check {
  display: grid;
  flex: none;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: rgba(92, 242, 165, 0.12);
  color: var(--rc-mint);
  box-shadow: 0 0 20px -4px rgba(92, 242, 165, 0.5);
}

.check svg {
  width: 18px;
  height: 18px;
}

.list h3 {
  margin: 4px 0 0;
  font-family: var(--rc-font-display);
  font-size: 1.2rem;
  font-weight: 650;
  letter-spacing: -0.015em;
  color: var(--rc-ivory);
}

.list p {
  margin: 6px 0 0;
  font-size: 15.5px;
  line-height: 1.6;
  color: var(--rc-mist);
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
