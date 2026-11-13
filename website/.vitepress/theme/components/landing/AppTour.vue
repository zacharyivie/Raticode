<script setup lang="ts">
import { computed, ref } from 'vue'
import { withBase } from 'vitepress'
import { screenshotImage, type ScreenshotName } from '../../../../data/screenshots'

interface Stop {
  id: string
  label: string
  tag?: string
  shot: ScreenshotName
  caption: string
}

const stops: Stop[] = [
  {
    id: 'workflows',
    label: 'Workflows',
    shot: 'studio-workflow-run',
    caption: 'A finished run on the graph. The tests failed once, the fix and review steps passed, and the pull request step ran. The timeline below lists every step.',
  },
  {
    id: 'rem',
    label: 'Rem',
    shot: 'rem-home',
    caption: 'Ask Rem in plain words. It answers, schedules recurring work and lists what comes next.',
  },
  {
    id: 'source',
    label: 'Rattish source',
    shot: 'workflow-source',
    caption: 'The same workflow as a text file you can read, diff and commit.',
  },
  {
    id: 'git',
    label: 'Source control',
    shot: 'source-control',
    caption: 'Review the agent’s change line by line, stage it and commit, all in the app.',
  },
  {
    id: 'brain',
    label: 'Second Brain',
    tag: 'Preview',
    shot: 'second-brain',
    caption: 'Search a notes folder and read each result with its matching passages highlighted.',
  },
]

const active = ref(0)
const current = computed(() => stops[active.value])
const tabs = ref<HTMLButtonElement[]>([])

const image = computed(() => screenshotImage(current.value.shot))
const src = computed(() => withBase(image.value.src))
const srcset = computed(() => image.value.sources.map((source) => `${withBase(source.path)} ${source.width}w`).join(', '))

function select(index: number, focus = false) {
  active.value = (index + stops.length) % stops.length
  if (focus) tabs.value[active.value]?.focus()
}

function onKey(event: KeyboardEvent) {
  const moves: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  if (event.key in moves) select(active.value + moves[event.key], true)
  else if (event.key === 'Home') select(0, true)
  else if (event.key === 'End') select(stops.length - 1, true)
  else return
  event.preventDefault()
}
</script>

<template>
  <section id="tour" class="rc-section tour" aria-labelledby="tour-title">
    <div class="rc-container">
      <div class="rc-section-head is-centered rc-reveal">
        <span class="rc-eyebrow">The real app</span>
        <h2 id="tour-title" class="rc-h2">See it <em>before you install.</em></h2>
      </div>

      <div class="tabs rc-reveal" role="tablist" aria-label="Parts of the app">
        <button
          v-for="(stop, index) in stops"
          :id="`tour-tab-${stop.id}`"
          :key="stop.id"
          ref="tabs"
          type="button"
          role="tab"
          class="tab"
          :class="{ 'is-on': index === active }"
          :aria-selected="index === active"
          :aria-controls="`tour-panel`"
          :tabindex="index === active ? 0 : -1"
          @click="select(index)"
          @keydown="onKey"
        >
          {{ stop.label }}<span v-if="stop.tag" class="tag">{{ stop.tag }}</span>
        </button>
      </div>

      <figure id="tour-panel" class="panel rc-reveal" role="tabpanel" :aria-labelledby="`tour-tab-${current.id}`">
        <a class="frame" :href="src" :aria-label="`Open the full-size screenshot: ${current.label}`">
          <img
            :key="current.shot"
            :src="src"
            :srcset="srcset"
            sizes="(min-width: 1200px) 1120px, calc(100vw - 40px)"
            :width="image.width"
            :height="image.height"
            :alt="image.alt"
            loading="lazy"
            decoding="async"
          />
        </a>
        <figcaption>
          <span class="caption">{{ current.caption }}</span>
          <span class="zoom-hint">Tap the screenshot to see it full size.</span>
          <span class="note">Demo project and data. Agent replies were scripted for the capture.</span>
        </figcaption>
      </figure>
    </div>
  </section>
</template>

<style scoped>
.tour {
  padding-bottom: clamp(48px, 6vw, 80px);
}

.tabs {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin: 40px auto 0;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 0 18px;
  border: 1px solid var(--rc-line-strong);
  border-radius: 999px;
  background: rgba(18, 16, 45, 0.6);
  font: 600 15px/1 var(--rc-font-body);
  color: var(--rc-ivory-dim);
  cursor: pointer;
  transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease;
}

.tab:hover {
  border-color: rgba(79, 240, 255, 0.45);
  color: var(--rc-ivory);
}

.tab.is-on {
  border-color: var(--rc-indigo-bright);
  background: linear-gradient(180deg, rgba(99, 83, 213, 0.55), rgba(99, 83, 213, 0.3));
  color: var(--rc-ivory);
  box-shadow: 0 0 24px -6px rgba(143, 130, 255, 0.7);
}

.tag {
  padding: 3px 7px;
  border-radius: 6px;
  background: rgba(255, 182, 72, 0.14);
  font: 600 12px/1.2 var(--rc-font-body);
  color: var(--rc-amber);
}

.panel {
  margin: 28px 0 0;
}

.frame {
  position: relative;
  display: block;
  overflow: hidden;
  border: 1px solid var(--rc-line-strong);
  border-radius: 14px;
  background: #18181b;
  box-shadow:
    0 40px 90px -40px rgba(99, 83, 213, 0.9),
    0 0 0 6px rgba(27, 24, 64, 0.55);
  line-height: 0;
}

.frame img {
  display: block;
  width: 100%;
  height: auto;
}

figcaption {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  max-width: 760px;
  margin: 22px auto 0;
  text-align: center;
}

.caption {
  font-size: clamp(1rem, 1.5vw, 1.1rem);
  line-height: 1.6;
  color: var(--rc-ivory-dim);
  text-wrap: pretty;
}

.note,
.zoom-hint {
  font-size: 13px;
  line-height: 1.5;
  color: #9a95bd;
}

.zoom-hint {
  display: none;
}

@media (max-width: 640px) {
  .zoom-hint {
    display: block;
  }

  .tabs {
    flex-wrap: nowrap;
    justify-content: flex-start;
    overflow-x: auto;
    margin-left: calc(-1 * clamp(20px, 4vw, 40px));
    margin-right: calc(-1 * clamp(20px, 4vw, 40px));
    padding: 4px clamp(20px, 4vw, 40px);
    scrollbar-width: none;
  }

  .tab {
    flex: none;
    min-height: 40px;
    padding: 0 14px;
    font-size: 14px;
  }

  .frame {
    border-radius: 10px;
    box-shadow: 0 24px 60px -30px rgba(99, 83, 213, 0.9);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tab {
    transition: none;
  }
}
</style>
