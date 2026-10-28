<script setup lang="ts">
import { computed, ref } from 'vue'
import WorkflowGraph from './WorkflowGraph.vue'
import { rattishSource, sourceRanges } from './workflow'

interface Token {
  text: string
  kind?: string
}

const KEYWORDS = new Set(['when', 'otherwise', 'failed'])

function tokenize(line: string): Token[] {
  const header = /^(node|workflow|rattish)(\s+[a-z-]+)?(:)(.*)$/.exec(line)
  if (header) {
    return [
      { text: header[1], kind: 'kw' },
      { text: header[2] ?? '', kind: 'id' },
      { text: header[3], kind: 'punct' },
      { text: header[4], kind: header[4].trim() ? 'num' : undefined },
    ]
  }
  const route = /^(\s+- )([a-z-]+)(.*)$/.exec(line)
  if (route) {
    const rest = route[3].split(/(\s+)/).map((part) => ({ text: part, kind: KEYWORDS.has(part) ? 'kw' : part.startsWith('node.') ? 'ref' : undefined }))
    return [{ text: route[1], kind: 'punct' }, { text: route[2], kind: 'id' }, ...rest]
  }
  const pair = /^(\s+)([a-z-]+)(:)(.*)$/.exec(line)
  if (pair) {
    const value = pair[4]
    const trimmed = value.trim()
    let kind: string | undefined = 'str'
    if (trimmed === 'true' || trimmed === 'false' || /^\d+$/.test(trimmed)) kind = 'num'
    else if (trimmed.startsWith('{')) kind = 'json'
    else if (trimmed === '|' || trimmed === 'pass') kind = 'kw'
    else if (pair[2] === 'type' || pair[2] === 'provider') kind = 'type'
    else if (pair[2] === 'to') kind = 'id'
    return [{ text: pair[1] }, { text: pair[2], kind: 'key' }, { text: pair[3], kind: 'punct' }, { text: value, kind }]
  }
  return [{ text: line, kind: line.trim() ? 'str' : undefined }]
}

const lines = rattishSource.split('\n').map((text, index) => {
  const owner = Object.entries(sourceRanges).find(([, [start, end]]) => index >= start && index <= end)?.[0] ?? null
  return { index, owner, tokens: tokenize(text) }
})

const focused = ref<string | null>(null)
const highlight = computed(() => focused.value)

const nodeNotes: Record<string, string> = {
  'run-tests': 'A shell step. allow-fail lets a failing test suite route to a fix instead of ending the run.',
  fix: 'Claude Code gets one focused job and a short prompt, not your whole chat history.',
  review: 'Codex returns typed output. approved: true sends the work back to the tests; anything else loops to fix.',
  'open-pr': 'Only reached when the tests pass. finish: pass ends the run successfully.',
}
</script>

<template>
  <section class="rc-section showcase" aria-labelledby="showcase-title">
    <div class="rc-container">
      <div class="rc-section-head rc-reveal">
        <span class="rc-eyebrow">One workflow, two views</span>
        <h2 id="showcase-title" class="rc-h2">Readable source. <em>Visible graph.</em></h2>
        <p class="rc-lede">
          Workflows are plain text you can diff, review and commit. The studio draws the same file as a live graph.
          Hover a node to see where it lives in the source.
        </p>
      </div>

      <div class="split rc-reveal">
        <div class="code" @mouseleave="focused = null">
          <div class="code-head">
            <span class="file">.raticode/fix-failing-tests/workflow.rattish</span>
            <span class="ok">✓ check passed</span>
          </div>
          <pre><code><span
            v-for="line in lines"
            :key="line.index"
            class="line"
            :class="{ 'is-hl': highlight && line.owner === highlight, 'is-dim': highlight && line.owner !== highlight }"
            @mouseenter="focused = line.owner"
          ><span class="ln">{{ line.index + 1 }}</span><span v-for="(token, t) in line.tokens" :key="t" :class="token.kind && `t-${token.kind}`">{{ token.text }}</span></span></code></pre>
        </div>

        <div class="graph-panel">
          <WorkflowGraph
            interactive
            :highlight="highlight"
            :states="{ 'run-tests': 'done', fix: 'done', review: 'done', 'open-pr': 'done' }"
            title="Graph of the Fix failing tests workflow"
            @focus-node="focused = $event"
          />
          <p class="note" aria-live="polite">
            <template v-if="highlight">
              <strong>{{ highlight }}</strong> {{ nodeNotes[highlight] }}
            </template>
            <template v-else>
              <strong>Try it.</strong> Hover or tab to a node. Two agents, two providers, one review loop, capped at 24 runs.
            </template>
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: 22px;
  margin-top: 56px;
  align-items: stretch;
}

.code,
.graph-panel {
  border: 1px solid var(--rc-line);
  border-radius: 22px;
  background: linear-gradient(170deg, rgba(22, 19, 56, 0.85), rgba(8, 7, 26, 0.95));
  box-shadow: 0 30px 80px -40px rgba(99, 83, 213, 0.8);
  overflow: hidden;
}

.code-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--rc-line);
  font-family: var(--rc-font-mono);
  font-size: 12px;
  color: var(--rc-mist);
}

.code-head .file {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.code-head .ok {
  flex: none;
  color: var(--rc-mint);
}

pre {
  margin: 0;
  padding: 16px 0;
  overflow-x: auto;
  font-family: var(--rc-font-mono);
  font-size: 12.5px;
  line-height: 1.6;
}

.line {
  display: block;
  padding: 0 18px 0 0;
  white-space: pre;
  color: var(--rc-ivory-dim);
  transition: background-color 0.25s ease, opacity 0.25s ease;
}

.line.is-hl {
  background: linear-gradient(90deg, rgba(79, 240, 255, 0.12), rgba(79, 240, 255, 0.02));
  box-shadow: inset 2px 0 0 var(--rc-cyan);
}

.line.is-dim {
  opacity: 0.4;
}

.ln {
  display: inline-block;
  width: 46px;
  padding-right: 16px;
  text-align: right;
  color: #4f4a72;
  user-select: none;
}

.t-kw {
  color: #ff8ad8;
}

.t-id {
  color: var(--rc-cyan);
}

.t-key {
  color: var(--rc-periwinkle);
}

.t-punct {
  color: #6d6894;
}

.t-str {
  color: var(--rc-ivory);
}

.t-num {
  color: var(--rc-amber);
}

.t-type {
  color: var(--rc-mint);
}

.t-json {
  color: #9f9ac2;
}

.t-ref {
  color: var(--rc-cyan);
}

.graph-panel {
  position: sticky;
  top: calc(var(--vp-nav-height, 64px) + 24px);
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 28px;
  padding: 44px 26px 26px;
  background:
    radial-gradient(rgba(185, 177, 255, 0.12) 1px, transparent 1px) 0 0 / 22px 22px,
    linear-gradient(170deg, rgba(22, 19, 56, 0.85), rgba(8, 7, 26, 0.95));
}

.graph-panel :deep(.graph) {
  margin: auto 0;
}

.note {
  min-height: 78px;
  margin: 0;
  padding: 14px 18px;
  border: 1px solid var(--rc-line);
  border-radius: 14px;
  background: rgba(5, 4, 13, 0.6);
  font-size: 15px;
  line-height: 1.55;
  color: var(--rc-mist);
}

.note strong {
  margin-right: 6px;
  font-family: var(--rc-font-mono);
  font-weight: 600;
  color: var(--rc-cyan);
}

@media (max-width: 960px) {
  .split {
    grid-template-columns: 1fr;
  }

  .graph-panel {
    position: static;
    order: -1;
  }
}
</style>
