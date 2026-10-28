<script setup lang="ts">
import { computed } from 'vue'
import { graphEdges, graphNodes, type NodeState } from './workflow'

const props = withDefaults(
  defineProps<{
    states?: Partial<Record<string, NodeState>>
    activeEdges?: string[]
    highlight?: string | null
    interactive?: boolean
    title?: string
  }>(),
  { states: () => ({}), activeEdges: () => [], highlight: null, interactive: false, title: 'Workflow graph' },
)

const emit = defineEmits<{ (e: 'focus-node', id: string | null): void }>()

const W = 176
const H = 58

const statusText: Record<NodeState, string> = {
  idle: 'Waiting',
  running: 'Running',
  done: 'Succeeded',
  failed: 'Failed',
}

const nodes = computed(() =>
  graphNodes.map((node) => {
    const state = props.states[node.id] ?? 'idle'
    return { ...node, state, status: statusText[state] }
  }),
)

const edges = computed(() =>
  graphEdges.map((edge) => ({
    ...edge,
    active: props.activeEdges.includes(edge.id),
    related: props.highlight !== null && (edge.from === props.highlight || edge.to === props.highlight),
  })),
)
</script>

<template>
  <svg class="graph" viewBox="0 0 640 300" role="img" :aria-label="title" :class="{ 'has-highlight': highlight }">
    <defs>
      <marker id="wg-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="rgba(185,177,255,.55)" />
      </marker>
      <marker id="wg-arrow-on" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0L10 5L0 10z" fill="#4ff0ff" />
      </marker>
      <filter id="wg-glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="5" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
    </defs>

    <g v-for="edge in edges" :key="edge.id" class="edge" :class="{ 'is-active': edge.active, 'is-related': edge.related }">
      <path class="edge-line" :d="edge.d" :marker-end="edge.active || edge.related ? 'url(#wg-arrow-on)' : 'url(#wg-arrow)'" />
      <path v-if="edge.active" class="edge-flow" :d="edge.d" pathLength="1" filter="url(#wg-glow)" />
      <g v-if="edge.label" class="edge-label" :transform="`translate(${edge.lx} ${edge.ly})`">
        <rect :x="-edge.label.length * 3.4 - 8" y="-10" :width="edge.label.length * 6.8 + 16" height="20" rx="10" />
        <text text-anchor="middle" dy="4">{{ edge.label }}</text>
      </g>
    </g>

    <g
      v-for="node in nodes"
      :key="node.id"
      class="node"
      :class="[`is-${node.state}`, { 'is-highlight': highlight === node.id, 'is-dim': highlight && highlight !== node.id }]"
      :transform="`translate(${node.x} ${node.y})`"
      :tabindex="interactive ? 0 : undefined"
      :role="interactive ? 'button' : undefined"
      :aria-label="interactive ? `${node.id}: ${node.kind}. ${node.detail}` : undefined"
      @mouseenter="interactive && emit('focus-node', node.id)"
      @mouseleave="interactive && emit('focus-node', null)"
      @focus="interactive && emit('focus-node', node.id)"
      @blur="interactive && emit('focus-node', null)"
    >
      <rect class="node-halo" x="-6" y="-6" :width="W + 12" :height="H + 12" rx="16" />
      <rect class="node-box" :width="W" :height="H" rx="12" />
      <rect class="node-icon" x="10" y="13" width="32" height="32" rx="8" />
      <text class="node-glyph" x="26" y="34" text-anchor="middle">{{ node.glyph }}</text>
      <text class="node-name" x="52" y="26">{{ node.id }}</text>
      <text class="node-detail" x="52" y="43">{{ node.detail }}</text>
      <circle class="node-dot" :cx="W - 14" cy="16" r="4" />
      <title>{{ node.id }} — {{ node.status }}</title>
    </g>
  </svg>
</template>

<style scoped>
.graph {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
  font-family: var(--rc-font-mono);
}

.edge-line {
  fill: none;
  stroke: rgba(185, 177, 255, 0.32);
  stroke-width: 2;
  transition: stroke 0.4s ease, opacity 0.4s ease;
}

.edge.is-active .edge-line,
.edge.is-related .edge-line {
  stroke: rgba(79, 240, 255, 0.75);
}

.has-highlight .edge:not(.is-related) {
  opacity: 0.35;
}

.edge-flow {
  fill: none;
  stroke: #4ff0ff;
  stroke-width: 3.5;
  stroke-linecap: round;
  stroke-dasharray: 0.18 0.82;
  animation: flow 0.9s linear infinite;
}

@keyframes flow {
  from {
    stroke-dashoffset: 1;
  }
  to {
    stroke-dashoffset: 0;
  }
}

.edge-label rect {
  fill: #0d0b24;
  stroke: rgba(185, 177, 255, 0.22);
}

.edge-label text {
  font-size: 10.5px;
  fill: #b9b1ff;
}

.edge.is-active .edge-label rect,
.edge.is-related .edge-label rect {
  stroke: rgba(79, 240, 255, 0.6);
}

.edge.is-active .edge-label text,
.edge.is-related .edge-label text {
  fill: #4ff0ff;
}

.node {
  outline: none;
  transition: opacity 0.35s ease;
}

.node[tabindex] {
  cursor: pointer;
}

.node.is-dim {
  opacity: 0.45;
}

.node-halo {
  fill: none;
  stroke: transparent;
  stroke-width: 2;
}

.node:focus-visible .node-halo {
  stroke: #4ff0ff;
}

.node-box {
  fill: #16133a;
  stroke: rgba(185, 177, 255, 0.3);
  stroke-width: 1.5;
  transition: stroke 0.35s ease, fill 0.35s ease;
}

.node-icon {
  fill: rgba(143, 130, 255, 0.16);
}

.node-glyph {
  font-size: 15px;
  font-weight: 700;
  fill: #b9b1ff;
}

.node-name {
  font-size: 13px;
  font-weight: 600;
  fill: #fdf2dc;
}

.node-detail {
  font-size: 10.5px;
  fill: #9f9ac2;
}

.node-dot {
  fill: rgba(185, 177, 255, 0.35);
  transition: fill 0.35s ease;
}

.node.is-highlight .node-box {
  stroke: #4ff0ff;
  fill: #1b1845;
}

.node.is-running .node-box {
  stroke: #4ff0ff;
  filter: drop-shadow(0 0 10px rgba(79, 240, 255, 0.55));
}

.node.is-running .node-dot {
  fill: #4ff0ff;
  animation: blink 0.8s ease-in-out infinite alternate;
}

.node.is-done .node-box {
  stroke: rgba(92, 242, 165, 0.75);
}

.node.is-done .node-dot {
  fill: #5cf2a5;
}

.node.is-failed .node-box {
  stroke: rgba(255, 107, 139, 0.85);
  filter: drop-shadow(0 0 10px rgba(255, 107, 139, 0.4));
}

.node.is-failed .node-dot {
  fill: #ff6b8b;
}

@keyframes blink {
  to {
    opacity: 0.25;
  }
}

@media (prefers-reduced-motion: reduce) {
  .edge-flow {
    animation: none;
    stroke-dasharray: none;
    opacity: 0.5;
  }

  .node.is-running .node-dot {
    animation: none;
  }
}
</style>
