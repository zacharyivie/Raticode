<script setup lang="ts">
const workspaceTabs = ['Files', 'Editor', 'Terminal', 'Git', 'Browser']
const swarm = Array.from({ length: 18 }, (_, index) => index)
</script>

<template>
  <section id="features" class="rc-section features" aria-labelledby="features-title">
    <div class="rc-container">
      <div class="rc-section-head is-centered rc-reveal">
        <span class="rc-eyebrow">Inside the studio</span>
        <h2 id="features-title" class="rc-h2">Everything your agents need. <em>Nothing they don't.</em></h2>
        <p class="rc-lede">
          A complete coding workspace built around one idea: small, focused agent steps beat one enormous conversation.
        </p>
      </div>

      <div class="bento">
        <article class="tile tile-rem rc-reveal">
          <div class="tile-copy">
            <span class="kicker">Rem</span>
            <h3>Switch models. Keep the thread.</h3>
            <p>
              Rem is the coding agent that lives in Raticode. Move a conversation from Claude Code to Codex mid-task.
              The history, project scope and resources stay with the thread.
            </p>
          </div>
          <div class="switcher" aria-hidden="true">
            <div class="model">
              <span class="model-label">Thread · flaky date test</span>
              <span class="model-roll">
                <span>Claude Code</span>
                <span>Codex</span>
                <span>Cursor</span>
                <span>Claude Code</span>
              </span>
            </div>
            <div class="thread">
              <p class="b is-user">Why does the date test fail on CI?</p>
              <p class="b"><small>Claude Code</small>It reads the local time zone. I pinned the test clock to UTC.</p>
              <span class="t-switch">switched to Codex · history kept</span>
              <p class="b is-user">Review that fix.</p>
              <p class="b"><small>Codex</small>Looks right. formatDay() in utils/time.ts has the same bug.</p>
            </div>
          </div>
        </article>

        <article class="tile tile-routes rc-reveal" style="--rc-delay: 80ms">
          <span class="kicker">Control flow</span>
          <h3>Branches, joins and cycles</h3>
          <p>Route on failure, on typed output, or otherwise. Wait for several steps with needs. Cap loops with max-runs.</p>
          <div class="chips" aria-hidden="true">
            <code>when failed</code><code>otherwise</code><code>needs</code><code>max-runs: 24</code>
          </div>
        </article>

        <article class="tile tile-loop rc-reveal" style="--rc-delay: 120ms">
          <span class="kicker">Fan-out</span>
          <h3>One step, many runs</h3>
          <p>Loop an agent over a count, the rows of a spreadsheet or every file in a folder, with a concurrency limit you choose.</p>
          <div class="stack" aria-hidden="true">
            <i /><i /><i /><b>×25</b>
          </div>
        </article>

        <article class="tile tile-schedule rc-reveal" style="--rc-delay: 60ms">
          <span class="kicker">Automation</span>
          <h3>Runs while you sleep</h3>
          <p>Cron schedules and file watchers start workflows without you.</p>
          <div class="cron" aria-hidden="true"><span>0 2 * * *</span><small>next run 02:00</small></div>
        </article>

        <article class="tile tile-workspace rc-reveal" style="--rc-delay: 100ms">
          <span class="kicker">Workspace</span>
          <h3>A real place to code</h3>
          <p>Files, editor, terminal, Git history, worktrees and a built-in browser sit next to your agent.</p>
          <div class="tabs" aria-hidden="true">
            <span v-for="(tab, index) in workspaceTabs" :key="tab" :class="{ 'is-on': index === 2 }">{{ tab }}</span>
          </div>
        </article>

        <article class="tile tile-swarm rc-reveal" style="--rc-delay: 140ms">
          <span class="kicker">Swarms</span>
          <h3>A team of agents</h3>
          <p>Split a job into tasks, give each agent its own workspace, then review what came back.</p>
          <div class="swarm" aria-hidden="true">
            <i v-for="cell in swarm" :key="cell" :style="{ animationDelay: `${(cell * 173) % 1800}ms` }" />
          </div>
        </article>

        <article class="tile tile-brain rc-reveal" style="--rc-delay: 80ms">
          <span class="kicker">Second Brain</span>
          <h3>Knowledge that sticks</h3>
          <p>Point Rem at a notes folder. It searches what you know and saves reports back as notes.</p>
          <ul class="notes" aria-hidden="true">
            <li><span>◆</span> release-checklist.md</li>
            <li class="is-new"><span>◆</span> flaky-test-report.html <em>saved</em></li>
            <li><span>◆</span> architecture-notes.md</li>
          </ul>
        </article>

        <article class="tile tile-cli rc-reveal" style="--rc-delay: 120ms">
          <span class="kicker">Headless</span>
          <h3>The same engine, no UI</h3>
          <p>Ship workflows to a server and run them with the standalone gof CLI on Linux, macOS or Windows.</p>
          <pre aria-hidden="true"><span>$</span> gof rattish run nightly-triage</pre>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.bento {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 18px;
  margin-top: 64px;
}

.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 260px;
  padding: 28px;
  border: 1px solid var(--rc-line);
  border-radius: 22px;
  background: linear-gradient(165deg, rgba(27, 24, 64, 0.78), rgba(9, 8, 28, 0.92));
  overflow: hidden;
  transition: border-color 0.35s ease, transform 0.35s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.tile::before {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  background: radial-gradient(400px 220px at var(--mx, 80%) -10%, rgba(143, 130, 255, 0.22), transparent 70%);
  pointer-events: none;
}

.tile:hover {
  border-color: rgba(185, 177, 255, 0.32);
}

.tile > * {
  position: relative;
}

.kicker {
  font-family: var(--rc-font-pixel);
  font-size: 11px;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--rc-cyan);
}

.tile h3 {
  margin: 14px 0 0;
  font-family: var(--rc-font-display);
  font-size: 1.5rem;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.025em;
  color: var(--rc-ivory);
}

.tile p {
  margin: 10px 0 0;
  font-size: 15.5px;
  line-height: 1.6;
  color: var(--rc-mist);
}

.tile-rem {
  grid-column: span 4;
  grid-row: span 2;
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 28px;
  background:
    radial-gradient(700px 400px at 100% 100%, rgba(79, 240, 255, 0.14), transparent 60%),
    linear-gradient(165deg, rgba(40, 33, 102, 0.85), rgba(9, 8, 28, 0.92));
}

.tile-rem h3 {
  font-size: clamp(1.8rem, 3vw, 2.4rem);
}

.tile-rem p {
  max-width: 520px;
  font-size: 17px;
}

.switcher {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px;
  border: 1px solid var(--rc-line);
  border-radius: 16px;
  background: rgba(5, 4, 13, 0.5);
}

.thread {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tile .b {
  max-width: 78%;
  margin: 0;
  padding: 9px 13px;
  border: 1px solid var(--rc-line);
  border-radius: 12px 12px 12px 4px;
  background: rgba(255, 255, 255, 0.035);
  font-size: 14px;
  line-height: 1.45;
  color: var(--rc-ivory-dim);
}

.tile .b small {
  display: block;
  margin-bottom: 2px;
  font-family: var(--rc-font-mono);
  font-size: 10.5px;
  color: var(--rc-cyan);
}

.tile .b.is-user {
  align-self: flex-end;
  border-color: rgba(143, 130, 255, 0.35);
  border-radius: 12px 12px 4px 12px;
  background: rgba(99, 83, 213, 0.32);
  color: var(--rc-ivory);
}

.t-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  font-family: var(--rc-font-mono);
  font-size: 11px;
  white-space: nowrap;
  color: var(--rc-cyan);
}

.t-switch::before,
.t-switch::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(79, 240, 255, 0.3);
}

.model {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--rc-line);
}

.model-label {
  font-family: var(--rc-font-mono);
  font-size: 11px;
  color: #8d88ad;
}

.model-roll {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: none;
  width: 160px;
  height: 44px;
  overflow: hidden;
  border: 1px solid rgba(79, 240, 255, 0.45);
  border-radius: 12px;
  box-shadow: 0 0 30px -6px rgba(79, 240, 255, 0.5);
}

.model-roll span {
  flex: none;
  display: flex;
  align-items: center;
  height: 44px;
  padding: 0 16px;
  font-family: var(--rc-font-display);
  font-size: 17px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--rc-ivory);
  animation: roll 7.5s cubic-bezier(0.7, 0, 0.3, 1) infinite;
}

@keyframes roll {
  0%,
  26% {
    transform: translateY(0);
  }
  33%,
  59% {
    transform: translateY(-100%);
  }
  66%,
  92% {
    transform: translateY(-200%);
  }
  100% {
    transform: translateY(-300%);
  }
}

.tile-routes,
.tile-loop {
  grid-column: span 2;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding-top: 22px;
}

.chips code {
  padding: 5px 10px;
  border: 1px solid var(--rc-line-strong);
  border-radius: 8px;
  background: rgba(5, 4, 13, 0.5);
  font-family: var(--rc-font-mono);
  font-size: 12px;
  color: var(--rc-periwinkle);
}

.chips code:first-child {
  border-color: rgba(255, 107, 139, 0.5);
  color: var(--rc-rose);
}

.stack {
  position: relative;
  height: 64px;
  margin-top: auto;
}

.stack i {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 70%;
  height: 40px;
  border: 1px solid var(--rc-line-strong);
  border-radius: 10px;
  background: #16133a;
}

.stack i:nth-child(1) {
  transform: translate(24px, -20px);
  opacity: 0.4;
}

.stack i:nth-child(2) {
  transform: translate(12px, -10px);
  opacity: 0.7;
}

.stack i:nth-child(3) {
  border-color: rgba(79, 240, 255, 0.5);
}

.stack b {
  position: absolute;
  right: 0;
  bottom: 4px;
  font-family: var(--rc-font-display);
  font-size: 2.2rem;
  font-weight: 800;
  letter-spacing: -0.04em;
  color: var(--rc-cyan);
  text-shadow: 0 0 24px rgba(79, 240, 255, 0.6);
}

.tile-schedule,
.tile-workspace,
.tile-swarm {
  grid-column: span 2;
}

.cron {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
  padding: 14px 16px;
  border: 1px solid rgba(255, 182, 72, 0.35);
  border-radius: 12px;
  background: rgba(255, 182, 72, 0.06);
}

.cron span {
  font-family: var(--rc-font-mono);
  font-size: 19px;
  font-weight: 600;
  color: var(--rc-amber);
}

.cron small {
  font-family: var(--rc-font-mono);
  font-size: 11px;
  color: #b9a27f;
}

.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  padding-top: 20px;
}

.tabs span {
  padding: 6px 11px;
  border-radius: 8px;
  background: rgba(185, 177, 255, 0.07);
  font-size: 12.5px;
  color: var(--rc-mist);
}

.tabs span.is-on {
  background: rgba(143, 130, 255, 0.26);
  color: var(--rc-ivory);
  box-shadow: inset 0 -2px 0 var(--rc-indigo-bright);
}

.swarm {
  display: grid;
  grid-template-columns: repeat(9, 1fr);
  gap: 6px;
  margin-top: auto;
  padding-top: 20px;
}

.swarm i {
  aspect-ratio: 1;
  border-radius: 4px;
  background: rgba(143, 130, 255, 0.2);
  animation: work 1.8s ease-in-out infinite;
}

@keyframes work {
  50% {
    background: rgba(79, 240, 255, 0.7);
    box-shadow: 0 0 10px rgba(79, 240, 255, 0.6);
  }
}

.tile-brain {
  grid-column: span 3;
  min-height: 200px;
  background:
    radial-gradient(500px 240px at 100% 0%, rgba(196, 92, 255, 0.16), transparent 60%),
    linear-gradient(165deg, rgba(27, 24, 64, 0.78), rgba(9, 8, 28, 0.92));
}

.notes {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: auto 0 0;
  padding: 20px 0 0;
  list-style: none;
  font-family: var(--rc-font-mono);
  font-size: 12.5px;
  color: var(--rc-mist);
}

.notes li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border: 1px solid var(--rc-line);
  border-radius: 10px;
  background: rgba(5, 4, 13, 0.45);
}

.notes span {
  font-size: 9px;
  color: #c45cff;
}

.notes .is-new {
  border-color: rgba(196, 92, 255, 0.45);
  color: var(--rc-ivory);
}

.notes em {
  margin-left: auto;
  font-style: normal;
  color: var(--rc-mint);
}

.tile-cli {
  grid-column: span 3;
  min-height: 200px;
}

.tile-cli pre {
  margin: auto 0 0;
  padding: 14px 16px;
  border: 1px solid var(--rc-line);
  border-radius: 12px;
  background: rgba(5, 4, 13, 0.7);
  font-family: var(--rc-font-mono);
  font-size: 13px;
  color: var(--rc-ivory);
  overflow: hidden;
}

.tile-cli pre span {
  color: var(--rc-periwinkle);
}

@media (max-width: 1020px) {
  .bento {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .tile-rem {
    grid-column: span 2;
    grid-row: auto;
  }

  .tile-routes,
  .tile-loop,
  .tile-schedule,
  .tile-workspace,
  .tile-swarm,
  .tile-brain,
  .tile-cli {
    grid-column: span 1;
  }
}

@media (max-width: 640px) {
  .bento {
    grid-template-columns: 1fr;
  }

  .tile-rem {
    grid-column: auto;
    padding: 24px 20px;
  }

  .switcher {
    padding: 16px;
  }

  .tile .b {
    max-width: 92%;
  }

  .tile {
    min-height: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .model-roll span,
  .swarm i {
    animation: none;
  }
}
</style>
