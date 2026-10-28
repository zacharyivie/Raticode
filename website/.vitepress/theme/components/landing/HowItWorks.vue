<script setup lang="ts">
const steps = [
  {
    n: '01',
    title: 'Describe it',
    body: 'Tell Rem what you want done. Rem drafts the workflow in Rattish, a small language you can read in one sitting, and checks it before you run anything.',
    visual: 'chat',
  },
  {
    n: '02',
    title: 'See it',
    body: 'Every workflow is a graph. Wire routes, joins, branches, loops and review cycles on the canvas. The graph and its source stay in sync.',
    visual: 'graph',
  },
  {
    n: '03',
    title: 'Run it anywhere',
    body: 'Run from the studio, on a cron schedule, when files change, or headless with the gof CLI on any machine. Every run keeps a snapshot you can inspect.',
    visual: 'cli',
  },
]
</script>

<template>
  <section id="how" class="rc-section how" aria-labelledby="how-title">
    <div class="rc-container">
      <div class="rc-section-head is-centered rc-reveal">
        <span class="rc-eyebrow">How it works</span>
        <h2 id="how-title" class="rc-h2">From a sentence to a <em>repeatable machine.</em></h2>
      </div>

      <ol class="steps">
        <li v-for="(step, index) in steps" :key="step.n" class="step rc-reveal" :style="{ '--rc-delay': `${index * 110}ms` }">
          <div class="visual" aria-hidden="true">
            <template v-if="step.visual === 'chat'">
              <div class="bubble is-user">Triage new issues every morning and draft fixes for the easy ones.</div>
              <div class="bubble"><span class="typing"><i /><i /><i /></span></div>
            </template>
            <template v-else-if="step.visual === 'graph'">
              <svg viewBox="0 0 240 120">
                <path class="v-edge" d="M50 60H100M140 60C160 60 160 30 190 30M140 60C160 60 160 90 190 90" />
                <path class="v-edge v-loop" d="M190 90C170 116 110 116 120 76" />
                <rect class="v-node" x="14" y="46" width="36" height="28" rx="8" />
                <rect class="v-node is-on" x="100" y="46" width="40" height="28" rx="8" />
                <rect class="v-node" x="190" y="16" width="36" height="28" rx="8" />
                <rect class="v-node is-warn" x="190" y="76" width="36" height="28" rx="8" />
              </svg>
            </template>
            <template v-else>
              <pre><span class="p">$</span> gof rattish run triage
<span class="ok">✓</span> fetch-issues   12 new
<span class="ok">✓</span> triage         4 easy
<span class="run">›</span> draft-fixes    looping 4×</pre>
            </template>
          </div>
          <span class="n">{{ step.n }}</span>
          <h3>{{ step.title }}</h3>
          <p>{{ step.body }}</p>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.steps {
  position: relative;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 22px;
  margin: 64px 0 0;
  padding: 0;
  list-style: none;
}

.steps::before {
  content: '';
  position: absolute;
  top: 100px;
  left: 8%;
  right: 8%;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(143, 130, 255, 0.5), rgba(79, 240, 255, 0.5), transparent);
}

.step {
  position: relative;
  padding: 0 6px;
}

.visual {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  height: 200px;
  padding: 22px;
  border: 1px solid var(--rc-line);
  border-radius: 20px;
  background: linear-gradient(170deg, rgba(27, 24, 64, 0.92), rgba(10, 9, 32, 0.96));
  box-shadow: 0 24px 60px -30px rgba(99, 83, 213, 0.8);
}

.bubble {
  max-width: 85%;
  padding: 10px 13px;
  border: 1px solid var(--rc-line);
  border-radius: 12px 12px 12px 4px;
  background: rgba(255, 255, 255, 0.04);
  font-size: 13.5px;
  line-height: 1.45;
  color: var(--rc-ivory-dim);
}

.bubble.is-user {
  align-self: flex-end;
  border-radius: 12px 12px 4px 12px;
  border-color: rgba(143, 130, 255, 0.35);
  background: rgba(99, 83, 213, 0.3);
  color: var(--rc-ivory);
}

.typing {
  display: inline-flex;
  gap: 5px;
}

.typing i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--rc-periwinkle);
  animation: bounce 1.2s ease-in-out infinite;
}

.typing i:nth-child(2) {
  animation-delay: 0.15s;
}

.typing i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes bounce {
  0%,
  60%,
  100% {
    transform: none;
    opacity: 0.4;
  }
  30% {
    transform: translateY(-5px);
    opacity: 1;
  }
}

svg {
  width: 100%;
  height: 100%;
}

.v-edge {
  fill: none;
  stroke: rgba(185, 177, 255, 0.45);
  stroke-width: 2;
}

.v-loop {
  stroke: var(--rc-amber);
  stroke-dasharray: 4 5;
  animation: dash 1.4s linear infinite;
}

@keyframes dash {
  to {
    stroke-dashoffset: -18;
  }
}

.v-node {
  fill: #1b1845;
  stroke: rgba(185, 177, 255, 0.45);
  stroke-width: 1.5;
}

.v-node.is-on {
  stroke: var(--rc-cyan);
  filter: drop-shadow(0 0 8px rgba(79, 240, 255, 0.6));
}

.v-node.is-warn {
  stroke: var(--rc-amber);
}

pre {
  margin: 0;
  font-family: var(--rc-font-mono);
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--rc-ivory-dim);
  white-space: pre;
  overflow: hidden;
}

pre .p {
  color: var(--rc-periwinkle);
}

pre .ok {
  color: var(--rc-mint);
}

pre .run {
  color: var(--rc-cyan);
}

.n {
  display: inline-block;
  margin-top: 28px;
  font-family: var(--rc-font-pixel);
  font-size: 13px;
  color: var(--rc-cyan);
}

h3 {
  margin: 10px 0 0;
  font-family: var(--rc-font-display);
  font-size: 1.8rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--rc-ivory);
}

p {
  margin: 12px 0 0;
  font-size: 16.5px;
  line-height: 1.65;
  color: var(--rc-mist);
}

@media (max-width: 900px) {
  .steps {
    grid-template-columns: 1fr;
    gap: 48px;
  }

  .steps::before {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .typing i,
  .v-loop {
    animation: none;
  }
}
</style>
