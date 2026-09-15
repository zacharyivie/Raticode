# Performance fixtures

These probes use generated data and test doubles. They never launch a provider or a user workflow. The baseline is commit `9aacc8e1a48004480ba0723c928ee0aaea7cecfc`; recorded samples are in `results/performance-2026-09-14.json`.

From the repository root:

```sh
uv sync --extra dev
PYTHONPATH=src .venv/bin/python benchmarks/swarm_performance.py
PYTHONPATH=src .venv/bin/python benchmarks/swarm_writes.py
node benchmarks/git_performance.cjs
```

The scheduler probe waits for a nominal 1.2 seconds with 0, 20, and 100 inactive teams. Each synthetic archive contains 512 KiB of padding. CPU is Python process CPU, not whole-desktop utilization. The writes probe measures ten state updates, cumulative SQLite WAL growth with automatic checkpointing disabled, and peak allocations traced during the updates. It excludes migration and fixture creation.

The Git probe uses a temporary real repository and the production Git runner, with ten unchanged 7 kB files. It records five warm concurrent refreshes and counts subprocesses. It does not invoke any user's Git repository.

For Chromium measurements, select the repository's Node version before npm:

```sh
source /home/doonk/.nvm/nvm.sh
nvm use
cd frontend
npm ci --include=dev
npm exec vite -- build --config benchmarks/vite.config.js
PERF_RESULT=/tmp/editor-performance.json xvfb-run -a node benchmarks/run.cjs
PERF_SPLIT=1 PERF_RESULT=/tmp/editor-split.json xvfb-run -a node benchmarks/run.cjs
PERF_SCALE=3 PERF_AVATAR=1 xvfb-run -a node benchmarks/run.cjs
```

The fixture mounts ten persistent text tabs. One holds 2,000,000 characters. It measures 80 Monaco edits, full-document reads and React commits; immediately saves and checks the exact contents; measures Git requests over 2.3 seconds; and opens another tab. It then decodes the four avatar bitmaps five times and disposes each bitmap. Renderer CPU covers that combined sequence. Retained heap comes from Chromium after an explicit garbage collection. The earlier `heapBytes` sample is not collected after GC and should not support a memory claim. Rendering uses Xvfb and software graphics, not a physical GPU.

For a before/after comparison, use the same benchmark files and dependencies in a separate checkout of the baseline commit. Alternate before and after process launches. The recorded results contain three runs per variant. Dynamic module URLs are incidental ephemeral loopback URLs. The split check opens a second pane and requires exactly two idle Git refreshes, keeping keyboard focus separate from visibility. Avatar checks capture all four poses at the requested device scale and at 100% and 150% zoom.

`frontend/benchmarks/bundle-budget.js` enforces a 1,000,000-byte entry budget, 400,000-byte optional chunk budget, and 3,050,000-byte Monaco budget during the normal production build. All limits use uncompressed JavaScript bytes. The ordinary Vite warning is advisory; this plugin fails builds that exceed their budgets.

`bash scripts/build-avatar-assets.sh` regenerates aligned 224 and 336 pixel delivery images using ImageMagick, preserving the original artwork and background filter.

The swarm runtime defaults to eight simultaneous turns across all teams. Set `GOFER_SWARM_MAX_CONCURRENCY` to a positive integer before starting the backend to change it. Each team's existing `maxConcurrency` still applies. Interactive Rem chat is outside this swarm budget.

Existing swarm databases migrate on next startup. The migration retains archives and delivery states; interrupted active runs still recover as paused with uncertain in-flight deliveries. The new persistence representation requires the updated code, so reverting to an older application requires a pre-migration database copy.
