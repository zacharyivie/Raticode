<script setup lang="ts">
import { buildMaze } from './maze'

const maze = buildMaze(34, 15, 44, 20261004)
</script>

<template>
  <div class="maze" aria-hidden="true">
    <svg
      :viewBox="`-44 -44 ${maze.width + 88} ${maze.height + 88}`"
      preserveAspectRatio="xMidYMid slice"
    >
      <defs>
        <linearGradient id="maze-comet" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stop-color="#8f82ff" />
          <stop offset="1" stop-color="#4ff0ff" />
        </linearGradient>
        <filter id="maze-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>
      <path class="maze-walls" :d="maze.walls" />
      <path class="maze-route" :d="maze.solution" />
      <path class="maze-comet maze-comet-glow" :d="maze.solution" pathLength="1" filter="url(#maze-glow)" />
      <path class="maze-comet" :d="maze.solution" pathLength="1" />
    </svg>
  </div>
</template>

<style scoped>
.maze {
  position: absolute;
  inset: 0;
  overflow: hidden;
  -webkit-mask-image: radial-gradient(ellipse 70% 62% at 50% 38%, #000 10%, rgba(0, 0, 0, 0.35) 55%, transparent 80%);
  mask-image: radial-gradient(ellipse 70% 62% at 50% 38%, #000 10%, rgba(0, 0, 0, 0.35) 55%, transparent 80%);
}

svg {
  width: 100%;
  height: 100%;
}

.maze-walls {
  fill: none;
  stroke: rgba(185, 177, 255, 0.13);
  stroke-width: 2;
  stroke-linecap: square;
}

.maze-route {
  fill: none;
  stroke: rgba(79, 240, 255, 0.06);
  stroke-width: 10;
  stroke-linejoin: round;
}

.maze-comet {
  fill: none;
  stroke: url(#maze-comet);
  stroke-width: 4;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 0.07 0.93;
  stroke-dashoffset: 0.07;
  animation: comet 11s linear infinite;
}

.maze-comet-glow {
  stroke-width: 14;
  opacity: 0.8;
}

@keyframes comet {
  to {
    stroke-dashoffset: -0.93;
  }
}

@media (prefers-reduced-motion: reduce) {
  .maze-comet {
    animation: none;
    stroke-dasharray: none;
    opacity: 0.35;
  }

  .maze-comet-glow {
    display: none;
  }
}
</style>
