# Paired-device release and acceptance

The desktop and Android source are ready for controlled acceptance testing.
Public release is blocked on independent security review, physical devices,
relay endurance and acceptance of the exact release packages. Tests with fake
providers and disposable credential stores do not close those gates.

## Current scope

The current implementation supports one home desktop per Android phone,
shared desktop threads, encrypted chat and scoped files, and user-started
background sync. Confirming a new phone pairing enables thread and project sync
by default; existing phones retain their saved setting. A shared phone acts as
the same user controlling desktop Rem, including changing provider permission
modes and creating or organizing threads. Desktop must stay open to synchronize
its archived history. Legacy individually scoped grants retain their limits.
See [mobile and desktop parity](../mobile-desktop-parity.md) for the current
sharing contract. Acceptance must cover both shared and legacy scoped grants.

Desktop Rem can delegate to paired desktop workers
when both the home thread and the target worker have the required local grants.
No phone can be a worker. Keep this feature experimental until two-host fleet
acceptance passes. Recommend Linux as the first advertised desktop platform;
macOS and Windows need their own package, credential-store and file-snapshot
acceptance before support is claimed.

LAN is preferred at connection or reconnection. A healthy relay connection does
not automatically migrate when Wi-Fi becomes available. Phones learn the current
desktop LAN endpoint through encrypted sync; `RATICODE_DEVICE_HOST` can override
automatic private-address selection. Fleet observations
expire after sixty seconds and report reachability and running job IDs. Missing
evidence means unknown. Phones can create shared conversations and change their
project, provider, model, effort and permission mode using the desktop catalog.
Rich fleet work descriptions, remote approval resolution and mobile swarm
controls remain outside this release scope. There is no Raticode SaaS dependency.

## Grant and revoke fleet execution

For individually scoped desktop-worker grants, open Settings > Paired devices
> Thread access and messages on the home desktop.
Choose the local project and thread, provider, model and permission mode. Leave
**Allow this thread to delegate work to paired desktops** unchecked for ordinary
chat. Check it and press **Grant this thread access** only when remote requests
on that thread should be allowed to ask Rem to send work to other desktops.
The saved grant displays whether fleet delegation is allowed or disabled.

Configure a separate local grant on each worker for the exact target thread and
project IDs. Fleet permission does not bypass provider permissions or target-local
policy. Read-only/plan permissions can still prohibit execution. Pairing and QR
scanning do not grant work authority.

**Revoke thread access** cancels accepted/running requests and removes the entire
grant, including fleet permission. Grant again with the checkbox unchecked to
restore chat-only access. Replacing a grant also cancels existing requests. Peer
revocation rejects future pinned sessions as well. Never retry uncertain work
under a new request ID without inspecting its previous outcome.

## Capacity and recovery

Desktop file offers reserve up to 50 MiB total and 128 files. Each file is at most
10 MiB and expires within 24 hours. Cancel unwanted transfers or wait for expiry;
the next file operation removes expired contents. Prefer LAN for file traffic.
Public ntfy allowances can make even a supported file size impractical.

Outbox payloads are capped at 10,000 events and 64 MiB per peer, including the new
encrypted payload being inserted. Reconnect the existing peer and refresh its
history to send an authenticated sync cursor. The desktop removes acknowledged
old events while retaining 400 recent entries. It never discards unacknowledged
responses. A failed insertion rolls back its acceptance record, so the same
original event can be retried after recovery without a second accepted operation.
Do not delete the registry or edit its tables to recover capacity.

Recent provider context is the last twenty dispatched/completed turns per peer
and thread. Transcripts retain a separate 64 MiB per-peer cap. Result payloads
retain recent entries independently of terminal request states. Lifetime event
and operation tombstones have a 100,000-entry cap per peer. Inbox entries include
sync and control events, so this is not a count of user messages. These records prevent
replay and are not automatically deleted. Reaching that lifetime cap requires
resolving any outstanding or uncertain outcomes locally, then retiring the
identity deliberately, revoking it on its peers and setting up a
new identity with new local grants. Preserve old records to inspect uncertain
work. This is an experimental-service limit, not unlimited history retention.
Before wider distribution, recommend a separately reviewed protocol epoch or
acknowledged replay-window design to make lifetime rollover routine. Do not
silently reset counters or discard revocation tombstones.

## Acceptance gates

Record the exact mobile and desktop revisions, artifact SHA-256, OS versions,
network route, test timestamps and pass/fail evidence. Keep invitations, private
keys, message text and account tokens out of the acceptance record.

| Gate | Required evidence | Current owner or next step |
| --- | --- | --- |
| Independent security review | Review enrollment/pins, TLS integration, relay framing, protected storage, replay, grants, file scope and revocation; resolve findings | Recommend a reviewer independent of the implementation team. User selects reviewer and budget. |
| Real phone and two desktops | Scan an actual QR, compare pins, deny then grant thread access, chat through the intended subscription, return a verified file, delegate harmless work, revoke mid-queue, restart and reconnect without duplicate work | User supplies Android 10+ phone and two distinct hosts and approves any provider use. |
| Network transitions | Wi-Fi to cellular and back, stale DHCP address, relay outage, delayed/duplicate delivery, interrupted pairing and reconnect | Test the documented reconnect-based LAN preference; do not claim automatic migration. |
| Relay and background endurance | At least 24 hours on a physical phone with idle/background and active periods; simulate 429/Retry-After and outages; deny notifications; exercise battery restrictions and service timeout | Recommend zero duplicate jobs, no lost acknowledged replies, and recovery within two minutes after a manual reconnect on a healthy route. Record actual latency and quota behavior, do not promise an unmeasured SLA. |
| Exact packages | Clean install, upgrade preserving keys/grants/history, real OS credential-store unlock/failure, scoped snapshots and Settings actions from the packaged Electron app | Start with Linux; repeat per OS before advertising support. |
| Distribution | Version identity, signing/notarization, update channel, support contact and disclosure process | User chooses ownership and distribution. Recommend internal testers first; no public upload until all release gates pass. |

A local test fix waits for the responder to commit the durable ready acknowledgment
before granting a thread. It does not weaken production active-peer checks and
is not evidence of physical pairing. No test should run a paid provider or a real
workflow just to satisfy this checklist.

## Prepare local artifacts without publishing

Use the committed lockfiles, a clean checkout and the configured OS build tools.
Select Node first for every npm invocation:

```bash
source "$HOME/.nvm/nvm.sh"
nvm use
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run check:build
xvfb-run -a npm --prefix frontend run test:pairing-browser
.venv/bin/python -m pytest tests/unit/test_device*.py -q
.venv/bin/ruff check src tests --fix
.venv/bin/mypy src tests
npm --prefix frontend run backend:build
(cd frontend && npx electron-builder --linux --dir --publish never)
(cd frontend && npx electron-builder --linux AppImage --publish never)
```

The backend wrapper includes the `devices` dependency extra. The frozen build
also includes the device protocol schema; release probes reject an archive
missing `gofer/devices/protocol/v2/event.schema.json`. An unpacked Linux
application and an unsigned AppImage are local acceptance artifacts. Neither
build success nor archive inspection verifies installation, upgrade or native
credential-store behavior.
Use `--publish never` explicitly for local packaging. Do not invoke an upload,
change the installed trusted CLI, or create production signing identities as
part of validation. Verify the actual package on the target OS and save its
hashes outside the source tree. Builds and signing credentials are not committed.

For current architecture and setup, read [device messaging](device-messaging.md)
and [device pairing](device-pairing.md). Historical protocol evidence remains
useful within its recorded revision and fixture boundaries.
