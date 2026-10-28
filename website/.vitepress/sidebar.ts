import type { DefaultTheme } from 'vitepress'

const page = (text: string, path: string): DefaultTheme.SidebarItem => ({ text, link: `/docs/${path}` })

export const docsSidebar: DefaultTheme.SidebarItem[] = [
  {
    text: 'Start here', collapsed: false,
    items: [page('Documentation', ''), page('Getting started', 'getting-started'), page('Installation', 'installation'), page('Provider setup', 'setup'), page('Your first workflow', 'first-workflow')],
  },
  {
    text: 'The studio', collapsed: false,
    items: [page('Projects & workspaces', 'studio/projects'), page('Code & graph editors', 'studio/editors'), page('Source control', 'studio/source-control'), page('Terminal', 'studio/terminal'), page('Integrated browser', 'studio/browser'), page('Runs & approvals', 'studio/runs'), page('Import & export', 'studio/sharing')],
  },
  {
    text: 'Working with Rem', collapsed: false,
    items: [page('Chat & threads', 'rem/chat'), page('Providers, models & effort', 'rem/providers'), page('Tools, skills & MCP', 'rem/resources'), page('Attachments & dictation', 'rem/attachments'), page('Usage & allowances', 'rem/usage')],
  },
  {
    text: 'Building workflows', collapsed: false,
    items: [page('Rattish language', 'workflows/rattish'), page('Routes, branches & joins', 'workflows/routes'), page('Inputs, outputs & bindings', 'workflows/inputs'), page('Loops & fan-out', 'workflows/loops'), page('Validation & preflight', 'workflows/validation'), page('Schedules', 'workflows/schedules'), page('File watchers', 'workflows/watchers'), page('Runner queue', 'workflows/runner-queue')],
  },
  {
    text: 'Node reference', collapsed: true,
    items: [page('Choose a node', 'nodes/'), ...[
      ['Commands & scripts', ['bash-command', 'shell-script', 'python-script']],
      ['Agents', ['agent', 'common-llm-task']],
      ['Files & resources', ['read-file', 'write-file', 'copy-file', 'move-file', 'delete-file', 'file', 'folder', 'open-resource', 'prompt-file']],
      ['Control & interaction', ['loop', 'break', 'workflow', 'approval-gate', 'notification', 'http-request']],
      ['Local retrieval', ['local-vectorize', 'local-search']],
    ].map(([text, names]) => ({ text: text as string, collapsed: true, items: (names as string[]).map(name => page(name, `nodes/${name}`)) }))],
  },
  {
    text: 'Knowledge & reports', collapsed: false,
    items: [page('Second Brain', 'knowledge/second-brain'), page('Conversation archive', 'knowledge/archive'), page('Reports, slides & PDF', 'knowledge/reports'), page('Report themes', 'knowledge/themes')],
  },
  {
    text: 'Teams & devices', collapsed: false,
    items: [page('Swarms', 'advanced/swarms'), page('Organizations', 'advanced/organizations'), page('Desktop fleet', 'advanced/fleet'), page('Mobile & pairing', 'advanced/mobile'), page('Permissions & local data', 'advanced/security')],
  },
  {
    text: 'Reference & help', collapsed: false,
    items: [page('CLI reference', 'reference/cli'), page('Settings', 'reference/settings'), page('Keyboard shortcuts', 'reference/shortcuts'), page('Troubleshooting', 'reference/troubleshooting')],
  },
]
