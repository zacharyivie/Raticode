# Rem steering and provider selection

While Rem responds, type a text instruction and press Enter or **Steer**. Shift+Enter inserts a line break. **Stop** remains a separate action. Steering can interrupt and restart the provider process; completed tool effects remain in the workspace.

The instruction stays in the composer until the server confirms acceptance. HTTP errors and uncertain connection failures preserve the draft. Retrying unchanged text after a connection failure reuses the request ID. Acceptance clears only that submitted draft; edits typed while the request is pending remain. Unsent text also stays with its thread when navigating between threads during the app session.

The steering list shows accepted, delivered, failed or cancelled receipts with the original provider and model. Delivered means the continuation received the instruction, not that the model obeyed it or finished the task. Returning to a thread or ending a stream reloads durable receipts. Failed instructions remain visible for review and are never replayed automatically.

Provider, model and permission controls are disabled during a response. The next turn can use another provider while retaining the Raticode conversation and resources. Unavailable saved providers remain selected and report their discovery error instead of silently switching. Raticode does not send provider-native session IDs between providers.

Rem, Swarm setup and Agent-node configuration share the catalog-driven provider/model picker. Providers advertising `supportsCustomModel` also show a custom model ID field. It keeps a local draft while focused and commits on blur or Enter. Empty input restores the selected model on blur. New CLI providers default to `default` permissions; catalog `permissionModes` and `defaultPermissionMode` take precedence. Catalog fixtures alone do not establish backend provider support.

See [the steering API](rem-steering-api.md) for durable receipt, generation, Stop and disconnect semantics.

## Verification

`frontend/src/pages/App.test.mjs` covers steering for Cursor, Copilot, OpenCode, Antigravity and Grok; exact continuation context in the next request; stale generations; unchanged retry IDs; separate Stop; HTTP 400/409/500 draft retention; failed receipt recovery without replay; provider/model selection and custom-model focus, clear, type, blur interactions across Rem, Swarms and Agent nodes; and unavailable-provider preservation.

The full `npm run test:browser` suite includes the rendered steering flow. `GOFER_STEERING_ONLY=1 npm run test:browser` isolates that flow and writes `/tmp/raticode-rem-steering.png`. It checks enabled text entry, locked provider selection, rejected draft retention, native Enter submission, receipt display and Stop preserving an unsent draft.

Select Node using the repository `.nvmrc` through nvm before every npm command. Required frontend scripts are `test`, `lint`, `check:build`, and `test:browser`. Browser launch and desktop subprocess checks require an environment that allows Electron and local sockets.
