# Desktop implementation handoff

V2 update: [the jointly selected TLS candidate](mobile-desktop-v2.md) supersedes the proposed Noise profile for new integration. The user authorized implementation and local network probes; independent security review remains outstanding. Existing demo behavior below remains unchanged.
The phone side goes first on the application contract. Use `protocol/v1` and `docs/mobile-desktop-contract.md` as the starting point. The schemas are proposals with passing local fixtures, not a production encrypted transport. This pass did not edit the desktop repository or execute any desktop work.

## Existing integration points inspected

Source root inspected: this repository's root.

| Existing code | Integration work |
| --- | --- |
| `src/gofer/ui/server.py`, chat request handling near `chat_jobs.start` | Extract/reuse a shared authenticated chat service; do not expose existing loopback HTTP routes to the relay |
| `src/gofer/ui/chat_jobs.py`, `ChatJobs.start` and `events` | Map canonical mobile request IDs to desktop conversation/turn IDs; adapt durable JSONL events to encrypted mobile events |
| `src/gofer/ui/chat.py`, `stream_workflow_chat` | Feed validated intent through the normal Rem provider flow with existing project context and approvals |
| `src/gofer/ui/chat_steering.py` | Reuse cancellation/steering semantics; return requested vs confirmed cancellation separately |
| `src/gofer/ui/chat_media.py`, `store_chat_attachments` and `resolve_chat_attachment` | Accept only authenticated, verified decrypted uploads; map opaque mobile file IDs to authorized desktop attachment records |
| `src/gofer/ui/swarms.py`, `swarm_store.py`, `rem_swarms.py` | Read authorized swarm state and human-inbox approvals for versioned snapshots; let Rem decide orchestration |
| `src/gofer/ui/server.py`, workflow approval handling | Route remote approval intents through existing policy checks; keep remote approval disabled by default |

`ChatJobs.start` already uses exclusive file creation to resist duplicate starts and a per-conversation active-turn guard. Its event cursor is per turn and starts at 1. Neither property substitutes for a durable paired-device inbox, a per-device outgoing sequence, or replay tombstones. Explicitly map the two sequence domains. Startup recovery must distinguish accepted-but-not-started work from work that already began. A duplicate request must retrieve the existing result instead of surfacing a file-exists exception or launching a new turn.

Desktop attachments currently use 32-character hex IDs and expose `storageName` internally. Wire IDs in this proposal are canonical hyphenated UUIDs. Keep an explicit mapping. Never send `storageName` or an arbitrary path to the phone. Desktop's existing larger attachment limits do not override mobile's 10 MiB negotiated limit.

## First deliverable: secure device service

Add a backend-owned device registry, independent of an open desktop window. Suggested new module group: `src/gofer/mobile/` with `devices`, `pairing`, `session`, `inbox`, `outbox`, `dispatch`, and `files`. These are proposed modules, not existing APIs.

The registry stores a device ID, pinned identity, label, creation/last-seen timestamps, revocation state, allowed capabilities, and references to OS-protected keys. Provide a Desktop settings page listing paired phones and an explicit revoke action. Store keys with an OS credential facility; no JSON file containing unprotected long-lived private keys or relay tokens. Do not log decoded event bodies or pairing payloads.

Add an explicit "Pair phone" action showing a five-minute QR invitation, device name, and key fingerprint. Support cancellation, expiry refresh, rate limiting, and one-time consumption. Reject a phone with an unsupported protocol. Require desktop confirmation of the authenticated identity before persisting trust. Do not add a pairing HTTP endpoint accessible from an untrusted network.

Resolve the Noise library/review gate described in the protocol document with Android and Python test vectors. Implement the proposed session control and opaque relay frame formats in the contract, starting with shared handshake and frame vectors. Verify wrong-key, wrong-PSK, altered prologue, replay, stale invitation, two-phone race, key loss, restart, and revoked-device behavior. Only then enable real network permissions on mobile.

## Second deliverable: durable bridge to Rem

Before acknowledging `chat.submit`, atomically store the authorized request and a work item. Deduplicate by paired-device ID plus request ID and compare content hashes on repeats. Enforce per-device and per-thread queue limits. Store a durable outgoing event before publishing it. The relay's response never counts as the desktop's acknowledgment.

Dispatch accepted messages into the existing Rem service. Apply desktop project/resource authorization before resolving IDs. The phone can ask "start a swarm, merge it, prepare a PR" as chat text; it does not get a general command-execution RPC. Provider and model changes update thread settings without changing the conversation ID, selected resources, history, or persona.

Add encrypted discovery/create/context-update and sync control messages during integration. They need revision preconditions and capability checks, not a direct mapping from the demo's free-text context fields. Return authoritative snapshots. Keep unknown IDs and unauthorized IDs indistinguishable where appropriate. Return stable bounded error codes without backend stack traces, paths, provider secrets, or raw tool output.

Map `ChatJobs.events` into bounded immutable `chat.message` chunks, request statuses, and a final result. Avoid mobile notification floods from token-by-token events. Persist cursor and application state together on receivers. Resume with the last committed per-device cursor; return an explicit snapshot-required response when history expired. Decide retention and storage quotas before production.

## Third deliverable: activity, files, approvals, and wake-up

Publish authorized swarm summaries and human-inbox items to their owning thread. Use monotonic revisions so an older snapshot cannot replace a newer one. An approval must bind its ID to the action digest, target thread/device, expiry, and policy. Deny remote execution approval unless that desktop policy explicitly allows it; a valid mobile signature never widens policy. Replayed or expired approvals must have no effect.

Authorize desktop file access using existing policies before generating a `file.offer`. Resolve symlinks and allowed roots at access time. Retain an immutable offered snapshot or reject changed files. Do not make Pictures or the home directory remotely browsable by default. Select and review the encrypted blob channel and streaming format; filename, MIME, keys, digests, and retrieval credentials stay inside E2EE. Both sides enforce actual byte limits and reject corrupt or partial files.

Verify ntfy hosting limits and Android background delivery with real devices. Keep notification messages generic; FCM, if needed, carries a wake-up hint only. Do not make a public notification click URL execute a desktop action. Handle no permission, battery restrictions, dead process, lost relay history, and offline desktops without inventing delivery confirmation.

## Acceptance sequence

1. Run the shared schema fixtures and negative cases in both languages. Add cryptographic cross-language vectors before enabling the relay adapter.
2. Pair a real phone; verify identity on desktop; revoke it; confirm it cannot reconnect or replay queued work.
3. Send one request twice, restart desktop between acceptance and dispatch, and prove at most one desktop work item exists.
4. Switch provider/model while retaining the same thread and context. Return a response to the original phone thread after desktop UI closes.
5. Cancel before acceptance, during execution, and after completion; show the actual resulting state in each case.
6. Disconnect/reconnect, reorder events, lose history, and rate-limit the relay. Recover without duplicate work or false receipts.
7. Transfer an authorized file and reject tampering, excessive size, expiry, revoked access, and an arbitrary requested path.
8. Attempt a merge/PR operation requiring approval. Confirm that pairing alone cannot authorize it and a stale approval does nothing.
9. Verify a background update on physical Android with notifications denied and allowed. Inspect relay observations and logs for sensitive content.

The concrete dependency for the next mobile integration pass is a runnable desktop peer with reviewed session encoding, cross-language vectors, and a test relay/blob configuration. No user account credential or final hosting choice was assumed in this pass.

## Optional Tailscale route

The desktop owns its listener and never manages the user's Tailscale account. To support the phone's Tailscale switch, it listens on its Tailscale address on the same device port as the Wi-Fi listener, after the user opts in, and answers `include_tailnet_endpoint` with `connection.tailnet_endpoint` (or `null` when not listening). Its own address checks must accept `100.64.0.0/10` for that listener only. See the [Tailscale route extension](mobile-desktop-v2.md#tailscale-route-extension-october-30-2026).

## Wake the computer

Both halves are on `dev`; the desktop half landed in `6342c4f` (Linux only, `src/gofer/devices/wake.py`). The phone's button stays hidden until a desktop sends `wake_mac`. This section is complete on its own. The background is in [Wake the computer](mobile-desktop-v2.md#wake-the-computer-november-9-2026) and the security note is in `docs/architecture.md`.

**The setting.** It is per phone and off by default.

1. Store it like desktop control: a table `device_wake (peer TEXT PRIMARY KEY)` created beside `device_desktop_control` in `src/gofer/devices/desktop_control.py`, with `enable`, `enabled` and `allowed` methods that follow the desktop-control ones. A row means on. Reject a device that isn't a `controller`, with `phone_required`.
2. `allowed(peer)` also requires desktop control for that peer. Add the table to the list at `src/gofer/devices/application.py:333` so unpairing removes it.
3. Add a `wake_computer` action in `src/gofer/devices/control.py` beside `desktop_control` (a non-boolean `enabled` raises `invalid_wake_computer_choice`), and report `wake_computer_peers` in `status()` as device IDs only, never the hardware address.
4. Add a toggle in `frontend/src/components/PairedDevices.jsx` beside **Use Raticode from this phone**, titled "Let this phone wake this computer" and disabled while that one is off. Its description must say it sends this computer's network card hardware address to the phone over the encrypted link, that the phone then sends an unencrypted wake signal on the local network, that it needs Wake-on-LAN enabled in the computer's firmware, and that it doesn't work over Tailscale.

**The field.** Add `wake_mac` at the top level of the `overview` result in `src/gofer/devices/desktop_control.py`, beside `rem_notices`.

- Type: a JSON string of exactly 17 characters matching `^[0-9a-f]{2}(:[0-9a-f]{2}){5}$`, lowercase and colon-separated, for example `aa:bb:cc:dd:ee:01`.
- Read it fresh each time `overview` is answered, so it follows network changes. Don't store it anywhere on disk.

**When the key is left out.** Leave `wake_mac` out of the object entirely, and never send `null` or an empty string, when any of these is true:

- The setting is off for this peer, including the default state.
- `self.app.lan_endpoint` is `None`, its `host` isn't a private IPv4 address, or `is_tailnet_address(host)` is true.
- No interface holds that exact address, or more than one physical interface does.
- The interface holding it isn't a physical network card: see step 3 below.
- The address can't be read, or any read raises `OSError`. `overview` must still answer normally.
- The address is all zeroes, all `ff`, has the multicast bit set (lowest bit of the first byte), or doesn't match the format.

**Choosing the network card.** The card is the one holding the phone's Wi-Fi route to this desktop, not the default route or the first card found. Put this behind one small function that tests can replace, such as `hardware_address(host: str) -> str | None`.

1. Take `host` from `self.app.lan_endpoint`, which is the address phones already connect to. It respects `RATICODE_DEVICE_HOST`.
2. On Linux, go through `socket.if_nameindex()` and read each interface's IPv4 address with `fcntl.ioctl` and `SIOCGIFADDR`. Keep the interfaces whose address equals `host` exactly. Add no new dependency.
3. Require `/sys/class/net/<name>/device` to exist. Bridges, bonds, `veth`, `tun`, WireGuard and `tailscale0` don't have it, and their address isn't one firmware listens for.
4. Read `/sys/class/net/<name>/address`, strip whitespace, lowercase it and validate it against the rules above.
5. A Wi-Fi card that holds the address is reported like any other. Few can wake, which is why the setting's description names the firmware requirement.
6. On macOS and Windows, return `None` and leave the key out until there is a reviewed way to read it there. Show the toggle as unavailable on those systems.

**No logging.** The hardware address must not appear in any log, audit entry, diagnostics export, `status()` answer, error message or exception text. That covers `src/gofer/devices/app_log.py` and the phone-visible Developer log. Log only that the setting changed, with the device ID. The address leaves the computer only inside this authenticated `overview` answer.

**Compatibility.** The phone sends no new request field, so `params` and `event.schema.json` don't change. `SHA256SUMS` and the pinned manifest hash in `tests/unit/test_device_protocol.py` stay as they are. Older phones read only the `overview` fields they know.

**Tests expected.** Put these in `tests/unit/test_device_desktop_control.py`, using the existing `phone` fixture and a replaced `hardware_address`, unless noted:

1. Off by default: with desktop control on and the setting never touched, `"wake_mac" not in overview`.
2. On: `overview["wake_mac"]` equals the address and matches the format.
3. Turned off again: the key is gone, not `null`.
4. Per phone: on for one paired phone and not in a second phone's `overview`.
5. Without desktop control: enabling is refused or has no effect, `overview` answers `unauthorized`, and the address doesn't appear. Turning desktop control off also stops the field.
6. Unpairing removes the row, and re-pairing the same phone starts off.
7. Non-phone device: `enable` raises `phone_required`.
8. Each "left out" case above: no endpoint, public address, Tailscale address, `hardware_address` returning `None`, and a raised `OSError`. `overview` still answers with its other fields.
9. Selection, as a separate test of the real function with faked interface tables and a temporary `/sys/class/net`: the interface holding `host` wins over one that comes first or holds the default route, an interface without `device` is refused, two physical interfaces holding it are refused, an uppercase address is lowercased, and all-zero, all-`ff` and multicast addresses are refused.
10. No logging: with `caplog` at `DEBUG`, toggling the setting and answering `overview` never prints the address, and `status()` has no address in it.
11. `control.py`: the `wake_computer` action accepts booleans, rejects anything else with `invalid_wake_computer_choice`, and appears in `wake_computer_peers`.
12. Frontend: the toggle starts off, is disabled while **Use Raticode from this phone** is off, and posts the `wake_computer` action.
13. Run the existing suites for device protocol, desktop control and application to confirm nothing else moved.

**After it lands.** Run the phone against this desktop. The button, a real Wi-Fi send and an actual wake are all still unverified.

## Rem alerts beyond this phone's own work

**Status: the desktop half is built, but the two halves haven't run together.** The desktop half is on desktop `dev` at `0d921e9` and the phone half is on mobile `dev` at `4252190`. A desktop from before that commit doesn't advertise support, so on it the phone's three new switches do nothing and its settings say to update Raticode on the computer. Section 10 hasn't been run, so the phone hasn't been tested against the desktop's own filter on an emulator or a real phone.

Sections 1 to 9 are the brief the desktop work was built from, in this repository, mostly `src/gofer/devices/desktop_control.py`: `rem.notices` in `_services`, `_rem_notices`, and the `overview` answer in `_read`. Section 10 is still to do. The rules the desktop settled on are in its `docs/mobile-desktop-parity.md`, under "Alerts for anything that needs you". The wider contract is in [Alerts for anything that needs you](mobile-desktop-v2.md#alerts-for-anything-that-needs-you-november-9-2026).

The desktop differs from this brief in two places:

- **Unusable marks (section 2).** A `mark` in the desktop's own form, `n1.<report seq>.<organization event seq>`, whose positions are past a cleared report or rebuilt database starts again from the current place, as asked. Any other string answers `invalid` instead of counting as no `mark`, and so does any field beside `include` other than `after` and `mark`. The phone only sends marks the desktop gave it, so this matters only if the mark's form changes later.
- **Round IDs (section 5).** The ID is `self-work-round-<session id>`, not the example `proposals-<session id>`.

### 1. Advertise support

Add `"rem_notices_filter": self.rem is not None` to the `overview` answer, beside `rem_notices`. The phone sends the new fields only after this reads `true`, and asks again on every connection.

### 2. The request

`desktop.read`, op `rem.notices`. `params` keeps `after` as it is today and gains two optional fields:

```json
{"after": 512, "include": ["needs_you", "finished", "proposals"], "mark": "opaque-from-last-answer"}
```

| Field | Values |
| --- | --- |
| `include` | Non-empty array of distinct strings from `needs_you` (switch **A task needs you**), `finished` (**A task finishes**) and `proposals` (**Proposals are waiting**). It names only the switches that are on, so any subset is valid. |
| `mark` | String of at most 256 characters, copied from the previous answer. Absent on the first filtered read. The phone sends it only with `include`. |

Validation:

- Answer `invalid` when `include` is not a non-empty list of distinct known names, or when `mark` is not a string of 1 to 256 characters.
- Treat a well-formed `mark` you can't use, such as one left from before a data reset, as no `mark`. Refusing it would block that phone's alerts for good, because the phone keeps its saved `mark` until an answer replaces it.
- Without `include`, behave exactly as today, with no `mark` and no `reason` in the answer.

### 3. The answer

With `include`, answer the usual `{cursor, notices, skipped, more}` and add `mark`, a string of at most 256 characters. The phone treats a longer one as absent. Always include `mark`, including when there is nothing new.

Each notice keeps `{id, kind, body, created_at, title, needs_user}`. `id` is at most 128 characters and `created_at` is ISO 8601 with an offset. Send at most 8 notices, oldest first, and count older ones left out in `skipped`, because the phone reads only the first 8. Set `more` when anything remains after this answer, in the message table or the other sources.

Filtered notices add `reason`, which is `needs_you`, `finished` or `proposals`. Give each notice one reason. `needs_you` wins over `finished` for a task that stopped waiting on the user. A message that counts because this phone asked for the work keeps today's rule and has no `reason`, even if it needs the user, so it alerts whichever switches are on.

`kind` tells the phone whether Rem's chat already shows the item:

| `kind` | Use for | While Rem's chat is open on the phone |
| --- | --- | --- |
| `task_update` | A task update stored in Rem's chat | Held back |
| `organization` | Any task that is **not** in Rem's chat: organization tasks **and self-work tickets**. The name is fixed in the phone. | Still alerts |
| `proposals` | One self-work round's batch, always with `reason: proposals` | Still alerts |

### 4. What counts is the desktop's decision

The phone doesn't judge what counts. It only filters by the switch named in `reason`. Cover tasks from every source, and return only reasons named in `include`:

| Source | Where it is today | `needs_you` | `finished` |
| --- | --- | --- | --- |
| Desktop-started Rem tasks | `task_update` messages, reached through the existing `after` cursor | `work.needs_user` is true | `work.state` is final, such as `succeeded`. Progress updates don't count, for example `recovery-*` messages. |
| Self-work tickets | **Not in Rem's chat.** `_finish` skips `enqueue_outcome` when `run["self_work"]` is set. They appear as `work_done` rows in `self_work_report`. | `state` is `blocked` | `state` is `done`. `researched` rows belong to proposals, not here. |
| Organizations | Organization tasks and their event log (`action == "events"` in `ui/organizations.py`) | Waiting on the user, such as `in_review` or a block `employee_may_unblock` says an employee can't clear. A `dependencies` block doesn't count. | `status` reaches `done` |

These are starting points, and the final rules are the desktop's. Check which states really mean "needs you" before relying on them. Don't reuse the phone's own-request rule for this: `device_rem_requests` is what keeps own-request alerts separate.

### 5. One `Proposals are waiting` item per self-work round

A round is one self-work session: `self_work_state["session"]`, created by `_begin_session` with a UUID in `id` and removed by `_end_session`.

- Send at most one `kind: "proposals"` notice per session that left proposals waiting, and none for a session that added none.
- Use a stable ID derived from the session, for example `proposals-<session id>`, in at most 128 characters. Proposals come from `ticket_added` report rows with `proposed: true` and `pending_proposal` tickets. Those rows don't record the session today, so store the session ID on them. `_ingest` can also run after the session has ended.
- The phone shows an ID once and remembers the last 16, so resending a round is harmless. Never create a second ID for the same round.
- Don't resend rounds older than the last 16, or the phone will alert for them again. The `mark` should stop that from happening.

### 6. Saved positions

`after` is the existing message position. `mark` is your own position in everything else, such as the highest `self_work_report.seq` and an organization event position. The phone treats it as opaque and it must fit in 256 characters, so check whether organization event sequences are global before encoding one per organization. Keep the position in `mark` and store nothing per phone.

| What the phone sends | What the desktop does |
| --- | --- |
| No `after` | The first read. Answer only the current `cursor` and, with `include`, the current `mark`, with no notices. |
| `include` with `after` and no `mark` | Report chat messages after `after` as usual. Report nothing from the other sources and return their current `mark`. |
| `include` with `after` and `mark` | Report everything after both positions. |
| A further switch was turned on | Nothing special. The phone keeps `after` and `mark`, and the next read just has a longer `include`, so older items in that category may be returned. The phone drops those until a read carrying that switch comes back with `more: false`, so don't add a per-switch baseline. Own-request replies from the same gap still alert. |
| Master switch turned on | The phone clears `after`, `mark` and its round IDs, so this looks like a first read. |
| No `include` | The answer has no `mark`, and the phone clears its saved one. A switch turned on later starts from the desktop's current place. |

### 7. Alert text

The phone decides what an alert says. Without **Show Rem's words**, it shows only fixed text: "A task needs you", "A task finished", "Proposals are waiting" and the existing "New message" and "Task update". With it on, the alert shows `body` as one plain line of up to 600 characters, falling back to the fixed text when `body` is blank. `title` is never shown, and the lock screen always says "New message from Rem". Keep `body` to what Rem's chat or the desktop's own task view would show, and don't log it.

### 8. Older desktops and phones stay as they are

- Don't loosen any check. `event.schema.json` keeps rejecting unknown event and payload fields with `additionalProperties: false`, and the phone's protection is never sending the filter to a desktop that doesn't advertise it.
- `params` is already an open object, so no schema or `SHA256SUMS` change is needed. If you add a fixture, update `SHA256SUMS` and the pinned hash in `tests/unit/test_device_protocol.py`. The phone reads the same files.
- Send `reason`, `mark` and the new `kind` values only when the read carried `include`, so older phones see the same answers as before.

### 9. Desktop tests

Add these beside `test_phone_alerts_cover_only_its_own_requests_and_their_tasks` in `tests/unit/test_device_desktop_control.py`:

- `overview` advertises the flag.
- Without `include`, the answer is unchanged.
- Each invalid `include` and `mark` is refused, and a stale well-formed `mark` is treated as absent.
- Each source produces the right `kind` and `reason`, and a reason not in `include` is left out.
- An own-request message has no `reason`.
- A session with proposals produces one notice with a stable ID, and a session without proposals produces none.
- `mark` advances, and no old items appear without one.
- The limit of 8, `skipped` and `more` hold across mixed sources.

### 10. The real phone-against-desktop check

`LiveRemAlertsTest` runs from `mobile/android` against `scripts/live-device-fixture.py`, using the desktop's source for the real device code. It needs an APK, so run it once. Keep it headless. It hasn't been run against the desktop's own filter yet.

**First, give the fixture real data.** `--alert-filter` replaces the desktop's answer with a script, so it can't prove the desktop's choices. `FixtureRem.messages_after` returns no messages, and the fixture has no self-work or organization data. Add a new fixture flag that leaves `DesktopControl._read` unwrapped and feeds the desktop's stores, revealing one step per filtered read:

| Read after all three switches are on | Data to reveal | What the test expects |
| --- | --- | --- |
| 1 | Nothing new | No alert |
| 2 | One task needing you, one finished task, one self-work round with proposals. Use different sources across runs. | Each of "A task needs you", "A task finished" and "Proposals are waiting" once, with no desktop text |
| 3 | Nothing new | Still one "Proposals are waiting" |
| 4 | One more finished task | A second "A task finished", posted silently in quiet hours with group `rem-quiet` |
| 5 | One task needing you with `body` exactly `Fixture words shown` | That text, because the test has turned on **Show Rem's words** |

Point the test's `remAlertsInvitationFile` run at the new flag. The phone reads about every 10 seconds, and each step in the test waits up to 45 seconds.

**Then run it** from `mobile/android`. Use a private emulator behind its own adb server so other workers' installs can't reach it, and keep temporary files out of `/tmp`:

```sh
WORK=/var/tmp/rem-alerts-real; mkdir -p "$WORK/tmp" "$WORK/new" "$WORK/old"
DESKTOP=$(git rev-parse --show-toplevel)            # on a branch that contains 0d921e9
git -C "$DESKTOP" archive e92aebe src rattish | tar -x -C "$WORK/old"   # the desktop from before this change
export ANDROID_ADB_SERVER_PORT=5099 ANDROID_SERIAL=emulator-5596 TMPDIR="$WORK/tmp" PYTHONDONTWRITEBYTECODE=1
export ANDROID_AVD_HOME="$HOME/.android/avd"   # or wherever the private AVD lives
adb start-server
emulator -avd Rem_Media_Pixel_8_API_36 -port 5596 -no-window -read-only -no-snapshot-save -no-audio -no-boot-anim &
adb wait-for-device shell 'while [ "$(getprop sys.boot_completed)" != 1 ]; do sleep 2; done; wm size 720x1280; wm density 360'

PYTHONPATH="$DESKTOP/src" RATICODE_FIXTURE_PORT=38684 RATICODE_FIXTURE_OUT="$WORK/new" \
  "$DESKTOP/.venv/bin/python" scripts/live-device-fixture.py --workspace --remote --desktop <new-real-data-flag> &
PYTHONPATH="$WORK/old/src" RATICODE_FIXTURE_PORT=38685 RATICODE_FIXTURE_OUT="$WORK/old" \
  "$DESKTOP/.venv/bin/python" scripts/live-device-fixture.py --workspace --remote --desktop --alert-filter-old &
sleep 8
adb push "$WORK/new/raticode-test-invitation.txt" /data/local/tmp/raticode-alerts-new.txt
adb push "$WORK/old/raticode-test-invitation.txt" /data/local/tmp/raticode-alerts-old.txt

./gradlew --no-daemon --max-workers=2 :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=dev.raticode.mobile.LiveRemAlertsTest \
  -Pandroid.testInstrumentationRunnerArguments.remAlertsInvitationFile=/data/local/tmp/raticode-alerts-new.txt \
  -Pandroid.testInstrumentationRunnerArguments.remAlertsOldInvitationFile=/data/local/tmp/raticode-alerts-old.txt
```

Each fixture accepts one pairing, so start fresh ones for every run. Stop them by the PIDs from `ss -ltnp` for those two ports. Don't use `pkill -f` with a pattern that also appears in your own command, and don't stop fixtures on any other port. Afterwards run `adb -s emulator-5596 emu kill`, `adb kill-server` and `rm -rf "$WORK"`.

`anOlderDesktopIsNeverSentTheFilter` uses `--alert-filter-old` with the pre-change desktop source, so its overview really has no flag.

**After the real check passes,** remove the `--alert-filter` branch of `fake_alert_filter` from `scripts/live-device-fixture.py` and point `docs/live-device-setup.md` at the new flag. Keep `--alert-filter-old`. Until then the stand-in stays.
