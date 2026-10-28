<script setup lang="ts">
import { withBase } from 'vitepress'

const pains = [
  {
    tag: 'Groundhog prompts',
    title: 'You type the same instructions every day.',
    body: 'Run the tests. Fix what broke. Summarize the diff. Open the PR. Your agent is brilliant, but your process lives in your clipboard.',
  },
  {
    tag: 'Context bloat',
    title: 'One endless chat carries everything.',
    body: 'Every step drags the whole conversation along. Answers drift, tool calls pile up, and the bill grows with the scrollback.',
  },
  {
    tag: 'Black-box runs',
    title: 'When it goes sideways, you start over.',
    body: 'There is no step to inspect, no failure to route around, no way to rerun just the part that went wrong.',
  },
]
</script>

<template>
  <section class="rc-section problem" aria-labelledby="problem-title">
    <div class="rc-container">
      <div class="rc-section-head rc-reveal">
        <span class="rc-eyebrow">The problem</span>
        <h2 id="problem-title" class="rc-h2">You became the <em>glue code</em> in your own AI workflow.</h2>
      </div>

      <div class="grid">
        <article v-for="(pain, index) in pains" :key="pain.tag" class="card rc-reveal" :style="{ '--rc-delay': `${index * 90}ms` }">
          <span class="tag">{{ String(index + 1).padStart(2, '0') }} · {{ pain.tag }}</span>
          <h3>{{ pain.title }}</h3>
          <p>{{ pain.body }}</p>
        </article>
      </div>

      <div class="turn rc-reveal">
        <img class="rc-pixel" :src="withBase('/brand/rem-think.webp')" alt="" width="543" height="640" loading="lazy" />
        <p>
          <strong>Raticode turns that loop into a machine you can see.</strong>
          Each step becomes a node with its own prompt, model and limits. Routes decide what happens next.
          Your agents do the work; you review the result.
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin-top: 56px;
}

.card {
  position: relative;
  padding: 30px 28px 32px;
  border: 1px solid var(--rc-line);
  border-radius: 20px;
  background: linear-gradient(170deg, rgba(27, 24, 64, 0.7), rgba(10, 9, 32, 0.7));
  overflow: hidden;
}

.card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255, 107, 139, 0.6), transparent);
}

.tag {
  font-family: var(--rc-font-pixel);
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--rc-rose);
}

h3 {
  margin: 18px 0 0;
  font-family: var(--rc-font-display);
  font-size: 1.45rem;
  font-weight: 650;
  line-height: 1.15;
  letter-spacing: -0.02em;
  color: var(--rc-ivory);
}

p {
  margin: 14px 0 0;
  font-size: 16px;
  line-height: 1.65;
  color: var(--rc-mist);
}

.turn {
  display: flex;
  align-items: center;
  gap: 32px;
  margin-top: 56px;
  padding: 28px 36px;
  border: 1px solid rgba(79, 240, 255, 0.22);
  border-radius: 24px;
  background:
    radial-gradient(600px 200px at 0% 50%, rgba(79, 240, 255, 0.12), transparent),
    rgba(12, 11, 34, 0.7);
}

.turn img {
  width: 92px;
  height: auto;
  flex: none;
}

.turn p {
  margin: 0;
  font-size: clamp(1.05rem, 1.7vw, 1.25rem);
  color: var(--rc-mist);
}

.turn strong {
  display: block;
  margin-bottom: 6px;
  font-family: var(--rc-font-display);
  font-size: clamp(1.3rem, 2.3vw, 1.7rem);
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--rc-ivory);
}

@media (max-width: 900px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .turn {
    flex-direction: column;
    align-items: flex-start;
    padding: 26px;
  }
}
</style>
