// Deterministic maze for the hero backdrop. A fixed seed keeps server and client
// renders identical, so the SVG hydrates without a mismatch.

export interface Maze {
  width: number
  height: number
  walls: string
  solution: string
}

function mulberry32(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const N = 1
const S = 2
const E = 4
const W = 8
const DIRS = [
  { bit: N, dx: 0, dy: -1, opposite: S },
  { bit: S, dx: 0, dy: 1, opposite: N },
  { bit: E, dx: 1, dy: 0, opposite: W },
  { bit: W, dx: -1, dy: 0, opposite: E },
]

export function buildMaze(cols: number, rows: number, cell: number, seed: number): Maze {
  const rand = mulberry32(seed)
  const open = new Uint8Array(cols * rows)
  const seen = new Uint8Array(cols * rows)
  const stack: number[] = [0]
  seen[0] = 1

  while (stack.length) {
    const current = stack[stack.length - 1]
    const x = current % cols
    const y = Math.floor(current / cols)
    const options = DIRS.filter(({ dx, dy }) => {
      const nx = x + dx
      const ny = y + dy
      return nx >= 0 && ny >= 0 && nx < cols && ny < rows && !seen[ny * cols + nx]
    })
    if (!options.length) {
      stack.pop()
      continue
    }
    const dir = options[Math.floor(rand() * options.length)]
    const next = (y + dir.dy) * cols + (x + dir.dx)
    open[current] |= dir.bit
    open[next] |= dir.opposite
    seen[next] = 1
    stack.push(next)
  }

  const startRow = Math.floor(rows / 2)
  const start = startRow * cols
  const end = startRow * cols + cols - 1

  const walls: string[] = []
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const cellBits = open[y * cols + x]
      const px = x * cell
      const py = y * cell
      if (!(cellBits & N)) walls.push(`M${px} ${py}h${cell}`)
      if (!(cellBits & W) && y * cols + x !== start) walls.push(`M${px} ${py}v${cell}`)
    }
  }
  for (let y = 0; y < rows; y++) {
    if (y * cols + cols - 1 !== end) walls.push(`M${cols * cell} ${y * cell}v${cell}`)
  }
  walls.push(`M0 ${rows * cell}h${cols * cell}`)

  // Breadth-first search gives the route the comet follows.
  const prev = new Int32Array(cols * rows).fill(-1)
  const queue = [start]
  prev[start] = start
  while (queue.length) {
    const current = queue.shift() as number
    if (current === end) break
    const x = current % cols
    const y = Math.floor(current / cols)
    for (const { bit, dx, dy } of DIRS) {
      if (!(open[current] & bit)) continue
      const next = (y + dy) * cols + (x + dx)
      if (prev[next] !== -1) continue
      prev[next] = current
      queue.push(next)
    }
  }

  const route: number[] = []
  for (let at = end; at !== start; at = prev[at]) route.push(at)
  route.push(start)
  route.reverse()

  const half = cell / 2
  const point = (index: number) => `${(index % cols) * cell + half} ${Math.floor(index / cols) * cell + half}`
  const solution =
    `M${-cell} ${startRow * cell + half}` +
    route.map((index) => `L${point(index)}`).join('') +
    `L${(cols + 1) * cell} ${startRow * cell + half}`

  return { width: cols * cell, height: rows * cell, walls: walls.join(''), solution }
}
