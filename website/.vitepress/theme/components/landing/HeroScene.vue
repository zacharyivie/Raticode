<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'
import WorkflowGraph from './WorkflowGraph.vue'
import type { NodeState } from './workflow'

interface Frame {
  states: Record<string, NodeState>
  edges: string[]
  log?: { tone: 'run' | 'ok' | 'fail' | 'route'; text: string }
  hold?: number
}

const idle: Record<string, NodeState> = { 'run-tests': 'idle', fix: 'idle', review: 'idle', 'open-pr': 'idle' }

// An illustrative run of the example workflow: tests fail, an agent fixes them,
// a second agent rejects the first patch, the retry passes and a PR is opened.
const frames: Frame[] = [
  { states: { ...idle }, edges: [], log: { tone: 'run', text: '$ gof rattish run fix-failing-tests' }, hold: 1300 },
  { states: { ...idle, 'run-tests': 'running' }, edges: [], log: { tone: 'run', text: 'run-tests   npm test' } },
  { states: { ...idle, 'run-tests': 'failed' }, edges: ['tests-fix'], log: { tone: 'fail', text: 'run-tests   3 failing  →  fix  (when failed)' } },
  { states: { ...idle, 'run-tests': 'failed', fix: 'running' }, edges: [], log: { tone: 'run', text: 'fix         claude-code is reading the failures' }, hold: 1700 },
  { states: { ...idle, 'run-tests': 'failed', fix: 'done' }, edges: ['fix-review'], log: { tone: 'ok', text: 'fix         patch ready  →  review' } },
  { states: { ...idle, 'run-tests': 'failed', fix: 'done', review: 'running' }, edges: [], log: { tone: 'run', text: 'review      codex is checking the diff' }, hold: 1500 },
  { states: { ...idle, 'run-tests': 'failed', fix: 'done', review: 'done' }, edges: ['review-fix'], log: { tone: 'route', text: 'review      approved: false  →  fix  (otherwise)' } },
  { states: { ...idle, 'run-tests': 'failed', fix: 'running', review: 'done' }, edges: [], log: { tone: 'run', text: 'fix         second pass' }, hold: 1500 },
  { states: { ...idle, 'run-tests': 'failed', fix: 'done', review: 'running' }, edges: ['fix-review'], log: { tone: 'run', text: 'review      codex is checking the diff' }, hold: 1400 },
  { states: { ...idle, 'run-tests': 'failed', fix: 'done', review: 'done' }, edges: ['review-tests'], log: { tone: 'ok', text: 'review      approved: true  →  run-tests' } },
  { states: { ...idle, 'run-tests': 'running', fix: 'done', review: 'done' }, edges: [], log: { tone: 'run', text: 'run-tests   npm test' } },
  { states: { 'run-tests': 'done', fix: 'done', review: 'done', 'open-pr': 'running' }, edges: ['tests-pr'], log: { tone: 'ok', text: 'run-tests   all passing  →  open-pr' } },
  { states: { 'run-tests': 'done', fix: 'done', review: 'done', 'open-pr': 'done' }, edges: [], log: { tone: 'ok', text: 'open-pr     pull request opened · finish: pass' }, hold: 4200 },
]

const finalFrame = frames.length - 1
const step = ref(finalFrame)
const logs = computed(() =>
  frames
    .slice(0, step.value + 1)
    .map((frame, index) => ({ ...frame.log!, index }))
    .slice(-5),
)
const current = computed(() => frames[step.value])
const running = computed(() => step.value < finalFrame)

let timer: ReturnType<typeof setTimeout> | undefined
let observer: IntersectionObserver | undefined
let visible = true

function schedule() {
  clearTimeout(timer)
  if (!visible) return
  timer = setTimeout(() => {
    step.value = step.value >= finalFrame ? 0 : step.value + 1
    schedule()
  }, frames[step.value].hold ?? 1050)
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  step.value = 0
  observer = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    if (visible) schedule()
    else clearTimeout(timer)
  })
  observer.observe(document.querySelector('.scene') as Element)
  schedule()
})

onBeforeUnmount(() => {
  clearTimeout(timer)
  observer?.disconnect()
})
</script>

<template>
  <div class="scene" role="img" aria-label="The Raticode desktop app running a workflow: tests fail, Claude Code fixes them, Codex reviews the fix, and a pull request is opened.">
    <div class="window" aria-hidden="true">
      <div class="titlebar">
        <span class="lights"><i /><i /><i /></span>
        <span class="tab is-active"><b>◇</b> fix-failing-tests</span>
        <span class="tab"><b>{ }</b> workflow.rattish</span>
        <span class="run-state" :class="{ 'is-running': running }">
          <i />{{ running ? 'Running' : 'Passed' }}
        </span>
      </div>

      <div class="body">
        <nav class="rail">
          <i class="is-on" /><i /><i /><i /><i />
        </nav>

        <div class="canvas">
          <div class="canvas-grid" />
          <WorkflowGraph :states="current.states" :active-edges="current.edges" />
          <div class="log">
            <TransitionGroup name="log">
              <p v-for="line in logs" :key="line.index" :class="`tone-${line.tone}`">
                <span class="mark">{{ line.tone === 'ok' ? '✓' : line.tone === 'fail' ? '✕' : line.tone === 'route' ? '↺' : '›' }}</span>{{ line.text }}
              </p>
            </TransitionGroup>
          </div>
        </div>

        <aside class="rem">
          <header>
            <img :src="withBase('/brand/rem-cyborg.webp')" alt="" class="rc-pixel" width="28" height="32" />
            <div><strong>Rem</strong><small>via Claude Code</small></div>
          </header>
          <div class="msgs">
            <p class="msg is-user">Every night: run the tests, fix what broke, get a second opinion, then open a PR.</p>
            <p class="msg">Built it. Four nodes with a review loop, capped at 24 runs so it can't spin forever.</p>
            <p class="msg is-chip"><span>◇</span> fix-failing-tests <em>added</em></p>
            <p class="msg">Scheduled for 02:00. I'll leave the PR for your morning review.</p>
          </div>
          <div class="composer">Ask Rem anything<span>↵</span></div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scene {
  position: relative;
  perspective: 1800px;
  text-align: left;
}

.window {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(196, 188, 255, 0.2);
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(22, 19, 56, 0.92), rgba(10, 9, 32, 0.96));
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.6),
    0 40px 120px -30px rgba(99, 83, 213, 0.7),
    0 0 160px -40px rgba(79, 240, 255, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
  transform: rotateX(10deg) scale(0.98);
  transform-origin: 50% 0%;
  transition: transform 1.2s cubic-bezier(0.2, 0.8, 0.2, 1);
  -webkit-backdrop-filter: blur(20px);
  backdrop-filter: blur(20px);
}

.scene:hover .window {
  transform: rotateX(4deg) scale(1);
}

.titlebar {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 44px;
  padding: 0 14px;
  border-bottom: 1px solid var(--rc-line);
  font-family: var(--rc-font-mono);
  font-size: 12px;
  color: var(--rc-mist);
}

.lights {
  display: flex;
  gap: 7px;
  margin-right: 14px;
}

.lights i {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: rgba(185, 177, 255, 0.22);
}

.lights i:first-child {
  background: #ff6b8b;
}

.lights i:nth-child(2) {
  background: #ffb648;
}

.lights i:nth-child(3) {
  background: #5cf2a5;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  height: 30px;
  padding: 0 12px;
  border-radius: 8px;
}

.tab b {
  color: var(--rc-periwinkle);
  font-weight: 500;
}

.tab.is-active {
  background: rgba(143, 130, 255, 0.12);
  color: var(--rc-ivory);
}

.run-state {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  padding: 5px 12px;
  border: 1px solid rgba(92, 242, 165, 0.4);
  border-radius: 999px;
  color: var(--rc-mint);
}

.run-state i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 10px currentColor;
}

.run-state.is-running {
  border-color: rgba(79, 240, 255, 0.45);
  color: var(--rc-cyan);
}

.run-state.is-running i {
  animation: pulse 0.9s ease-in-out infinite alternate;
}

@keyframes pulse {
  to {
    opacity: 0.2;
  }
}

.body {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) 300px;
  min-height: 470px;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-top: 18px;
  border-right: 1px solid var(--rc-line);
  background: rgba(5, 4, 13, 0.35);
}

.rail i {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  border: 1.5px solid rgba(185, 177, 255, 0.25);
}

.rail i.is-on {
  border-color: var(--rc-indigo-bright);
  background: rgba(143, 130, 255, 0.25);
  box-shadow: 0 0 16px rgba(143, 130, 255, 0.5);
}

.canvas {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 18px;
  padding: 34px 34px 18px;
}

.canvas-grid {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(rgba(185, 177, 255, 0.16) 1px, transparent 1px);
  background-size: 22px 22px;
  -webkit-mask-image: linear-gradient(180deg, #000 60%, transparent);
  mask-image: linear-gradient(180deg, #000 60%, transparent);
  pointer-events: none;
}

.canvas :deep(.graph) {
  position: relative;
  max-width: 600px;
  margin: 0 auto;
}

.log {
  position: relative;
  height: 118px;
  overflow: hidden;
  padding: 10px 14px;
  border: 1px solid var(--rc-line);
  border-radius: 12px;
  background: rgba(5, 4, 13, 0.7);
  font-family: var(--rc-font-mono);
  font-size: 12px;
  line-height: 1.6;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.log p {
  margin: 0;
  white-space: pre;
  color: var(--rc-mist);
}

.log .mark {
  display: inline-block;
  width: 18px;
  font-weight: 700;
}

.tone-ok .mark {
  color: var(--rc-mint);
}

.tone-fail .mark {
  color: var(--rc-rose);
}

.tone-route .mark {
  color: var(--rc-amber);
}

.tone-run .mark {
  color: var(--rc-cyan);
}

.log p:last-child {
  color: var(--rc-ivory);
}

.log-enter-active {
  transition: all 0.4s ease;
}

.log-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.log-leave-active {
  display: none;
}

.rem {
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--rc-line);
  background: linear-gradient(180deg, rgba(143, 130, 255, 0.07), rgba(5, 4, 13, 0.2));
}

.rem header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--rc-line);
}

.rem header img {
  width: 28px;
  height: auto;
}

.rem header div {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.rem header strong {
  font-family: var(--rc-font-display);
  font-size: 15px;
  color: var(--rc-ivory);
}

.rem header small {
  font-family: var(--rc-font-mono);
  font-size: 10.5px;
  color: var(--rc-mist);
}

.msgs {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
}

.msg {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--rc-line);
  border-radius: 12px 12px 12px 4px;
  background: rgba(255, 255, 255, 0.03);
  font-size: 13px;
  line-height: 1.5;
  color: var(--rc-ivory-dim);
}

.msg.is-user {
  align-self: flex-end;
  max-width: 88%;
  border-radius: 12px 12px 4px 12px;
  border-color: rgba(143, 130, 255, 0.35);
  background: rgba(99, 83, 213, 0.28);
  color: var(--rc-ivory);
}

.msg.is-chip {
  align-self: flex-start;
  display: inline-flex;
  gap: 8px;
  padding: 7px 12px;
  border-radius: 999px;
  border-color: rgba(79, 240, 255, 0.35);
  font-family: var(--rc-font-mono);
  font-size: 11.5px;
  color: var(--rc-ivory);
}

.msg.is-chip span {
  color: var(--rc-cyan);
}

.msg.is-chip em {
  font-style: normal;
  color: var(--rc-mint);
}

.composer {
  display: flex;
  justify-content: space-between;
  margin: 0 16px 16px;
  padding: 11px 14px;
  border: 1px solid var(--rc-line-strong);
  border-radius: 14px;
  font-size: 13px;
  color: #77729a;
}

.composer span {
  color: var(--rc-periwinkle);
}

@media (max-width: 1020px) {
  .body {
    grid-template-columns: 44px minmax(0, 1fr);
  }

  .rem {
    display: none;
  }
}

@media (max-width: 640px) {
  .window {
    transform: none;
  }

  .body {
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
  }

  .rail,
  .tab:not(.is-active) {
    display: none;
  }

  .canvas {
    padding: 22px 12px 12px;
  }

  .log {
    height: 104px;
    font-size: 10.5px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .window,
  .scene:hover .window {
    transform: none;
    transition: none;
  }

  .run-state.is-running i {
    animation: none;
  }
}
</style>
