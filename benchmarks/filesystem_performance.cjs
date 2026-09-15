// Run with the project's Node version. An optional root is searched read-only.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { performance, monitorEventLoopDelay } = require('node:perf_hooks');
const { scanProject } = require('../frontend/electron/project-search.cjs');
const { createAppLog } = require('../frontend/electron/app-log.cjs');
const { readGitStatus } = require('../frontend/electron/git-status.cjs');
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

(async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'taskurotta-io-bench-'));
  const root = process.argv[2] ? fs.realpathSync(process.argv[2]) : path.join(temporary, 'project');
  try {
    if (!process.argv[2]) {
      fs.mkdirSync(root);
      for (let i = 0; i < 1000; i++) fs.writeFileSync(path.join(root, `${String(i).padStart(4, '0')}.txt`), `${'ordinary text\n'.repeat(100)}${i % 10 === 0 ? 'benchmark needle\n' : ''}`);
    }
    const samples = { 1: [], 2: [], 4: [] };
    let reference;
    // Warm each mode, then rotate execution order to reduce cache/order bias.
    for (let round = 0; round < 6; round++) for (const concurrency of [[1, 2, 4], [4, 2, 1]][round % 2]) {
      const start = performance.now();
      const result = await scanProject(root, { query: 'benchmark needle', readConcurrency: concurrency });
      const elapsed = performance.now() - start;
      const signature = JSON.stringify(result);
      if (reference && signature !== reference) throw new Error('Search results differ; stop changing the benchmark directory.');
      reference = signature;
      if (round) samples[concurrency].push(elapsed);
    }
    const log = createAppLog(path.join(temporary, 'logs'), { maxBytes: 5 * 1024 * 1024 });
    const delay = monitorEventLoopDelay({ resolution: 10 }); delay.enable();
    await new Promise(resolve => setTimeout(resolve, 20));
    const start = performance.now();
    let accepted = 0;
    for (let i = 0; i < 5000; i++) accepted += Number(log.write('info', 'benchmark', `message ${i}`));
    const enqueueMs = performance.now() - start;
    await log.close();
    const flushMs = performance.now() - start;
    delay.disable();
    const statusStart = performance.now();
    const status = await readGitStatus(root);
    const statusMs = performance.now() - statusStart;
    console.log(JSON.stringify({ platform: process.platform, arch: process.arch, node: process.version,
      root: process.argv[2] ? root : 'temporary 1000-file fixture',
      search: Object.fromEntries(Object.entries(samples).map(([pool, values]) => [pool, { medianMs: median(values), samplesMs: values }])),
      log: { attempted: 5000, accepted, enqueueMs, flushMs, eventLoopP99Ms: delay.percentile(99) / 1e6 },
      git: { active: status.active, statusMs },
      caveat: 'Local warm-cache probe. Use an idle local NTFS project on native Windows for a default-policy decision.'
    }, null, 2));
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
