// Screenshots of the real desktop app, captured headlessly from an isolated demo profile
// (fictional project, people and notes; stand-in provider CLIs). Window captures are 1440 x 900,
// dark theme, saved as WebP at 960 and 1440 px wide in public/images/screenshots/.
// A cropped capture lists its own size and widths. phone-* images come from the real Android app
// on a headless emulator paired with the repository's disposable desktop peer, at 360 and 720 px.

export interface Screenshot {
  alt: string
  width?: number
  height?: number
  widths?: readonly number[]
}

export const screenshots = {
  'studio-workflow-run': {
    alt: 'The Raticode studio after a run of the Fix failing tests workflow. The graph shows run-tests (one allowed failure), fix, review and open-pr, each marked as succeeded. The run timeline lists each step below it, and a Rem thread on the right explains the workflow it drafted.',
  },
  'workflow-source': {
    alt: 'The workflow.rattish file for Fix failing tests open in the code editor, with the run-tests, fix and review nodes and their routes, next to the Rem thread.',
  },
  'run-preview': {
    width: 760,
    height: 746,
    widths: [760],
    alt: 'The Run preview dialog for Fix failing tests: destructive actions per node, the provider CLIs each agent node needs, Local or Remote execution, and the execution order by generation.',
  },
  'source-control': {
    alt: 'Source control with src/time.js staged and a commit message typed. The editor shows the inline diff: one added line passes the shop time zone to the date formatter.',
  },
  'rem-home': {
    alt: "Rem's home. The conversation shows a summary of this week's changes and Rem confirming a weekly check and a research task. The sidebar lists three upcoming tasks with their next run times.",
  },
  'rem-thread': {
    alt: 'A Rem thread in the right pane with its steps expanded: Read package.json, Write the workflow file, and Running bash commands, followed by Rem\'s summary of the workflow it drafted.',
  },
  board: {
    alt: "The Kanban board with five tickets for the acme-storefront project, two in Backlog and three in To do, and Rem's report of the tickets it added.",
  },
  organizations: {
    alt: 'The Workers chart of a paused organization named Acme Studio: you as owner, an engineering lead, and a builder, a reviewer and a researcher who report to the lead.',
  },
  'browser-tabs': {
    alt: 'The Browser plugin with five tabs in one tab bar, each with a badge for the browser it runs in: Acme Storefront (chromium), Acme docs (Firefox), Release notes (chromium), Acme status (Chrome) and Release notes (gecko). The active Raticode (chromium) tab shows a demo coffee shop page below the address bar.',
  },
  'browser-tab-groups': {
    width: 1019,
    height: 76,
    widths: [1019],
    alt: "The Browser plugin's tab bar. Two Raticode (chromium) tabs sit together with a + after them, then the private tabs with their own +. + Browser is open and offers Raticode (gecko), Firefox, Chrome and Edge, the browsers with no tabs yet. Chrome and Edge are greyed out because they aren't installed.",
  },
  'settings-browser': {
    width: 780,
    height: 650,
    widths: [780],
    alt: 'Settings open on the Browser category, with every category listed on the left. Default browser for new tabs is set to Raticode (chromium), with Firefox found on the computer. Below it are Settings for each browser with a Choose a browser menu, the Home page and the Search URL.',
  },
  'settings-browser-rem': {
    width: 780,
    height: 650,
    widths: [780],
    alt: "The Rem section of Settings > Browser. Rem's browser is set to Raticode browser tabs. Sites where Rem uses your sign-ins and Sites Rem may not open are None, Sites Rem may open is Any site, Approve Rem's browser actions asks on signed-in and shared tabs, Let Rem run page scripts is on, and Downloads started by Rem is set to Block.",
  },
  'phone-rem': {
    width: 720,
    height: 1600,
    widths: [360, 720],
    alt: "Rem's Tasks on the phone, grouped like the desktop. Under Working on: Audit the phone UI against the desktop (running the unit tests) and Check last night's backups (waiting for a free slot). Under Needs you: a branch question in amber and a failed build in red. Coming up: a weekly dependency report. Rem, Threads, Orgs, Swarms and More are on the bottom bar.",
  },
  'phone-browser-tabs': {
    width: 720,
    height: 1600,
    widths: [360, 720],
    alt: "The phone browser's tab list. New tabs open in offers Raticode (chromium) and Raticode (gecko), and Open this page in offers Chrome. The list holds an example.com tab marked chromium and an example.org tab marked gecko. Below, On your computer lists two of the desktop's browser tabs.",
  },
  'phone-pairing': {
    width: 720,
    height: 1600,
    widths: [360, 720],
    alt: "The phone's Pair with Disposable emulator desktop? dialog over the Connect to your desktop screen. It shows the desktop fingerprint to compare with the invitation, notes that the desktop must also confirm the phone and that it connects over Wi-Fi, and has Cancel and Pair buttons.",
  },
  'rem-task-checks': {
    width: 1000,
    height: 900,
    widths: [1000],
    alt: 'The task "Show delivery estimates on the product page" open in Rem\'s sidebar after its run couldn\'t finish. Under "3 of 7 passed, 2 failed, 1 unresolved, 1 not checked", Failed lists two checks with the reviewer\'s reasons, Unresolved one, Not checked one, and Passed is folded. The chat shows the same run\'s card with Retry review and Run again.',
  },
  'rem-task-waiting': {
    alt: 'The one-time task "Update the changelog for 1.5" open in Rem\'s sidebar. An hourglass says it is waiting for "Show the order cut-off time at checkout" to finish, which is shown as Running, with a Start now button above the task\'s fields.',
  },
  'ticket-pills': {
    width: 760,
    height: 480,
    widths: [760],
    alt: 'Rem\'s chat answering what is left on the board. Each ticket key, REM-1, REM-3, REM-5, REM-4 and REM-2, is a pill with a status mark and the ticket\'s title, and the task card for REM-6 lists its ticket as a pill too.',
  },
  'ticket-history': {
    alt: 'The Kanban board with ticket REM-4, "Add tests for holiday shipping", open on the right. Its History and comments show the review blocking it with one failed check out of four, your comment asking for Thanksgiving to be computed, your move back to To do, and the next run moving to In review and then Done with all four checks passed.',
  },
  'selfwork-settings': {
    width: 780,
    height: 650,
    widths: [780],
    alt: 'Settings, Rem tab, Self-work section: Work on my own after 15 quiet minutes is on, Max threads is 2, Pull backlog tickets when threads are free is off, and acme-storefront is ticked under Rem\'s projects. Below are the running task and the latest report of tickets added, finished and blocked.',
  },
  'org-work-schedule': {
    alt: 'The Organization tab of Acme Studio with "New feature work" in a pill next to its name. The Work schedule panel shows 7 blocks in Europe/London: New feature work now until 17:00, Application hardening next, a 24-hour bar of the daily blocks, and the weekly release, monthly dependency audit and quarterly security review below.',
  },
  'brain-map': {
    alt: 'The Second Brain map for a demo notes folder: four topic bubbles sized by document count, joined by one solid written link and dashed suggested links. The selected topic, Product page, lists its six documents and marks Product page speed as the most connected.',
  },
  'graph-inspector': {
    alt: 'The Fix failing tests graph with the run-tests node selected. The Node inspector on the right is on its Action tab, showing the bash command npm test with its working folder and environment fields.',
  },
  'workflow-triggers': {
    width: 787,
    height: 832,
    widths: [787],
    alt: 'Workflow settings opened beside the graph, scrolled to Triggers: a Running in Raticode status, the Start at login switch, a weekday 9:00 schedule in America/Chicago, a file trigger on the test folder for *.test.js, and an Add trigger button.',
  },
  'second-brain': {
    alt: 'Second Brain search for "delivery date" across a notes folder: four matching documents with highlighted passages on the left and the top result open in the reader on the right.',
  },
} satisfies Record<string, Screenshot>

export type ScreenshotName = keyof typeof screenshots

// Size, file widths and paths of one screenshot, with paths relative to the site base.
export function screenshotImage(name: ScreenshotName) {
  const shot: Screenshot = screenshots[name]
  const widths = shot.widths ?? [960, 1440]
  const path = (width: number) => `/images/screenshots/${name}-${width}.webp`
  return {
    alt: shot.alt,
    width: shot.width ?? 1440,
    height: shot.height ?? 900,
    src: path(widths[widths.length - 1]),
    sources: widths.map((width) => ({ path: path(width), width })),
  }
}
