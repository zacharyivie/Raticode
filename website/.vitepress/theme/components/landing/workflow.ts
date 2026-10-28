// The example workflow shown on the landing page. The Rattish source below passes
// `gof rattish check` and `gof rattish preflight`; keep the graph in step with it.

export type NodeState = 'idle' | 'running' | 'done' | 'failed'

export const rattishSource = `rattish: 1

workflow:
  name: Fix failing tests
  max-runs: 24

node run-tests:
  type: bash-command
  start: true
  allow-fail: true
  command: npm test
  to:
    - fix when failed
    - open-pr otherwise

node fix:
  type: agent
  provider: claude-code
  prompt: |
    Run npm test, find the root cause of each failure and fix it.
    Keep the diff small.
  to: review

node review:
  type: agent
  provider: codex
  prompt: Review the diff. Approve only if it fixes the cause.
  output-schema: {"properties": {"approved": {"type": "boolean"}}, "required": ["approved"], "type": "object"}
  to:
    - run-tests when node.review.output.approved
    - fix otherwise

node open-pr:
  type: bash-command
  command: gh pr create --fill
  finish: pass`

export interface GraphNode {
  id: string
  x: number
  y: number
  glyph: string
  kind: string
  detail: string
}

export interface GraphEdge {
  id: string
  from: string
  to: string
  d: string
  label?: string
  lx?: number
  ly?: number
}

export const graphNodes: GraphNode[] = [
  { id: 'run-tests', x: 12, y: 121, glyph: '$', kind: 'bash command', detail: 'bash · npm test' },
  { id: 'fix', x: 232, y: 18, glyph: '✦', kind: 'agent', detail: 'agent · Claude Code' },
  { id: 'review', x: 452, y: 121, glyph: '✦', kind: 'agent', detail: 'agent · Codex' },
  { id: 'open-pr', x: 232, y: 224, glyph: '$', kind: 'bash command', detail: 'bash · gh pr create' },
]

export const graphEdges: GraphEdge[] = [
  { id: 'tests-fix', from: 'run-tests', to: 'fix', d: 'M100 121C100 72 156 47 224 47', label: 'when failed', lx: 110, ly: 72 },
  { id: 'fix-review', from: 'fix', to: 'review', d: 'M408 47C488 47 540 70 540 113' },
  { id: 'review-fix', from: 'review', to: 'fix', d: 'M452 140C400 140 350 124 336 84', label: 'otherwise', lx: 404, ly: 126 },
  { id: 'review-tests', from: 'review', to: 'run-tests', d: 'M452 166C380 194 270 194 196 166', label: 'approved', lx: 322, ly: 188 },
  { id: 'tests-pr', from: 'run-tests', to: 'open-pr', d: 'M100 179C100 230 156 253 224 253', label: 'otherwise', lx: 110, ly: 228 },
]

// Which lines of the source belong to each node, for linked highlighting.
export const sourceRanges: Record<string, [number, number]> = (() => {
  const lines = rattishSource.split('\n')
  const ranges: Record<string, [number, number]> = {}
  let current: string | null = null
  let start = 0
  lines.forEach((line, index) => {
    const match = /^node ([a-z-]+):/.exec(line)
    if (match) {
      if (current) ranges[current] = [start, index - 2]
      current = match[1]
      start = index
    }
  })
  if (current) ranges[current] = [start, lines.length - 1]
  return ranges
})()
