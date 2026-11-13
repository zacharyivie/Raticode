#!/usr/bin/env python3
"""Disposable emulator Wi-Fi peer. Uses this repository's desktop source unless PYTHONPATH names another; never uses user keys/APIs."""
import asyncio
import os
import pathlib
import tempfile
import sys
from uuid import uuid4
if not os.environ.get("PYTHONPATH"):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))
from gofer.devices.registry import DeviceRegistry
from gofer.devices.pairing import Invitations
from gofer.devices.service import DeviceListener
from gofer.devices.application import DeviceApplication
from gofer.ui.device_chat import DeviceChatBridge
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering

# Another fixture may already be running for a parallel test: RATICODE_FIXTURE_PORT and RATICODE_FIXTURE_OUT
# move this one's port and its /tmp/raticode-test-* files.
PORT = int(os.environ.get("RATICODE_FIXTURE_PORT", "38473"))
OUT = os.environ.get("RATICODE_FIXTURE_OUT", "/tmp")

class FixtureSwarms:
    """In-memory swarm service for --remote. No agents, providers or workspaces run."""
    def __init__(self, root):
        self.root = str(pathlib.Path(root).resolve())
        self.swarm = {"id": "fixture-swarm", "name": "Fixture swarm", "agents": [{"id": "lead", "name": "Lead"}],
                      "run": {"state": "running", "task": "Prepare the fixture release", "progress": {"percent": 25.0},
                              "agentStates": {"lead": {"state": "working"}},
                              "messages": [{"senderId": "lead", "body": "Started the release checklist", "createdAt": "now"}]
                              + ([{"senderId": "lead", "createdAt": "now", "body": "\n".join(
                                  f"Step {n}: checked the release notes, the changelog and the version files for section {n}." for n in range(1, 16))}]
                                 if "--ui-tour" in sys.argv else []),
                              "humanInbox": [{"id": "ask", "state": "pending", "description": "Publish the fixture notes?",
                                              "recommendedAction": "Publish", "createdAt": "now"}]}}
    def list(self, root):
        return [self.swarm] if str(pathlib.Path(root).resolve()) == self.root else []
    def get(self, root, identifier, **_):
        return self.swarm
    def control(self, root, identifier, action, **_):
        self.swarm["run"]["state"] = {"pause": "paused", "resume": "running", "stop": "stopped"}[action]
    def message(self, root, identifier, payload):
        self.swarm["run"]["messages"].append({"senderId": "user", "body": payload["body"], "createdAt": "now"})
    def execution(self, root, identifier, payload):
        self.swarm["run"]["humanInbox"][0]["state"] = "addressed"
        self.swarm["run"]["messages"].append({"senderId": "user", "body": "Human response: " + (payload.get("instruction") or "Publish"), "createdAt": "now"})

def work_transcript():
    """Mostly thought records, as a real worker writes them; see desktop_control._thread_entries."""
    def steps(prefix, count):
        out = []
        for i in range(count):
            tid = f"{prefix}-tool-{i}"
            out += [{"id": f"{tid}-a", "role": "assistant", "kind": "thought", "body": "Thinking",
                     "trace": {"id": f"{prefix}-think-{i}", "kind": "summary", "phase": "start", "status": "running", "title": "Thinking"}},
                    {"id": f"{tid}-b", "role": "assistant", "kind": "thought", "body": "Thought",
                     "trace": {"id": f"{prefix}-think-{i}", "kind": "summary", "phase": "result", "status": "complete", "title": "Thought", "detail": "for 2s"}},
                    {"id": f"{tid}-c", "role": "assistant", "kind": "thought", "body": "Bash",
                     "trace": {"id": tid, "kind": "tool", "phase": "start", "status": "running", "title": "Bash", "input": "{}"}},
                    {"id": f"{tid}-d", "role": "assistant", "kind": "thought", "body": "Bash",
                     "trace": {"id": tid, "kind": "tool", "phase": "update", "status": "running", "title": "Bash",
                               "detail": "Run the unit tests" if i == count - 1 else f"Look through file {i}", "command": f"./gradlew test --tests Case{i}"}},
                    {"id": f"{tid}-e", "role": "assistant", "kind": "thought", "body": "Tool result",
                     "trace": {"id": tid, "kind": "tool", "phase": "result", "status": "complete", "title": "Tool result", "output": "BUILD SUCCESSFUL"}}]
        out.append({"id": f"{prefix}-say", "role": "assistant", "kind": "thought", "body": "x",
                    "trace": {"kind": "summary", "title": "Summary", "body": "The thread view groups thoughts now; checking small screens next."}})
        return out
    instruction = ("Investigate and fix the poor mobile display when opening work threads from tasks in Raticode Mobile. "
                   "Trace the task-to-work-thread navigation and resulting thread view, compare with the desktop experience, "
                   "and make it usable on phone-sized screens.\n\n" + "\n".join(f"- Background note {i}: keep the desktop's behavior." for i in range(14)))
    report = ("## Work threads fit the phone\n\nThe phone now groups each run of thoughts, like the desktop.\n\n"
              "| Check | Result |\n|---|---|\n| Unit tests | 152 passed |\n| Emulator | Passed |\n\n"
              "```\n./gradlew assembleDebug testDebugUnitTest lintDebug connectedDebugAndroidTest --info --stacktrace\n```\n\n"
              "See https://example.com/a/very/long/link/that/should/wrap/or/scroll/on/a/narrow/phone/screen/without/breaking/the/layout")
    return ([{"id": "ask-1", "role": "user", "sender": "rem", "body": instruction, "createdAt": "2026-10-27T10:00:00+00:00"}]
            + steps("r1", 70)
            + [{"id": "final-1", "role": "assistant", "kind": "final", "body": report},
               {"id": "ask-2", "role": "user", "sender": "rem", "body": "Please also check the loading state.", "createdAt": "2026-10-27T10:30:00+00:00"}]
            + steps("r2", 3)
            + [{"id": "r2-live", "role": "assistant", "kind": "thought", "body": "Bash",
                "trace": {"id": "r2-live", "kind": "tool", "phase": "start", "status": "running", "title": "Bash", "detail": "Build the app"}}])

def tour_transcript():
    """--ui-tour: a worker thread shaped like a real one: Rem's long brief, work, a long report, Rem's review request and a long verdict."""
    def steps(prefix, count):
        out = []
        for i in range(count):
            out.append({"id": f"{prefix}-{i}", "role": "assistant", "kind": "thought", "body": "Bash",
                        "trace": {"id": f"{prefix}-{i}", "kind": "tool", "phase": "result", "status": "complete", "title": "Bash",
                                  "detail": f"Read screen file {i}", "command": f"sed -n 1,200p Screen{i}.kt"}})
        return out
    brief = ("The user asked: 'Go through the phone app and find where it looks rough next to the desktop app.'\n\n"
             "Work in the Raticode repository as one thread covering both desktop and mobile. Read AGENTS.md and the worktree skill first.\n\n"
             + "\n".join(f"{n}. Compare screen {n} with the desktop and note every mismatch in hierarchy, spacing, status and empty states." for n in range(1, 9))
             + "\n\nReport the findings list, what changed, revisions, checks actually run, and anything unverified.")
    sections = []
    for n in range(1, 7):
        sections.append(f"## Finding {n}: screen {n} reads as one block\n\nThe phone shows every line of the result at once, so the actions sit "
                        f"several screens below the summary. The desktop folds the same content behind a preview.\n\n"
                        f"- Evidence: `ui/Screen{n}.kt:{40 + n}`\n- Desktop: `components/Screen{n}.jsx:{12 + n}`\n- Fix: fold to a preview with Show more.")
    report = ("The phone's screens now fold long text and show task status the way the desktop does. Nothing was pushed.\n\n"
              + "\n\n".join(sections)
              + "\n\n| Check | Result |\n|---|---|\n| Unit tests | 673 passed |\n| Lint | 0 errors |\n| Emulator | 6 of 6 passed |")
    verdict = ("Produce a prioritized audit with evidence: pass. I re-read each cited line and they match.\n\n"
               + "\n\n".join(f"Criterion {n}: pass. The screen folds long text, keeps its actions in reach, and the status reads as a glyph and words. "
                               f"I checked the code path and the screenshot for screen {n}, and the behavior matches the report." for n in range(1, 6))
               + "\n\nNo repair needed.")
    return ([{"id": "tour-ask", "role": "user", "sender": "rem", "body": brief}]
            + steps("tour-a", 24)
            + [{"id": "tour-report", "role": "assistant", "kind": "final", "body": report},
               {"id": "tour-review", "role": "user", "sender": "rem", "body": "Please check that this work is complete: " + brief}]
            + steps("tour-b", 9)
            + [{"id": "tour-verdict", "role": "assistant", "kind": "final", "body": verdict}])

def explorer_fixture(base):
    """Sample files of every kind the desktop opens, in "Explorer samples" inside the fixture project, for the file explorer."""
    import shutil, struct, subprocess, wave, zlib
    root = base / "Explorer samples"
    for folder in ("code", "media", "docs", "logs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Explorer samples\n\nA **Markdown** note with a [link](https://example.invalid).\n\n- one\n- two\n")
    (root / "code" / "app.kt").write_text("/* The fixture app,\n   in two lines */\nfun main() {\n    val greeting = \"hello\"\n    println(greeting)  // say it\n}\n"
                                          + "val longLine = \"" + "wrap " * 60 + "\"\n")
    (root / "code" / "server.py").write_text("def handler(request):\n    return {'ok': True}  # fixture\n")
    (root / ".env").write_text("FIXTURE_TOKEN=not-a-real-secret\n")
    (root / "logs" / "big.log").write_text("".join(f"{n:06d} fixture log line\n" for n in range(20000)))
    (root / "docs" / "page.html").write_text("<!doctype html><title>Page</title><h1>Fixture page</h1><p>Static HTML.</p>"
                                            "<script>document.body.innerHTML='script ran'</script><img src='https://example.invalid/x.png'>")
    (root / "docs" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><circle cx='60' cy='60' r='50' fill='#7b2ff7'/></svg>")
    (root / "docs" / "deck.docx").write_bytes(b"PK\x03\x04" + bytes(range(256)) * 4)
    # A one-page PDF with a line of text.
    stream = b"BT /F1 24 Tf 72 720 Td (Fixture PDF page) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    (root / "docs" / "guide.pdf").write_bytes(pdf)
    # A small PNG gradient.
    width, height = 96, 64
    raw = b"".join(b"\0" + bytes(v for x in range(width) for v in (x * 2, y * 3, 160)) for y in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    (root / "media" / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                                               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    with wave.open(str(root / "media" / "tone.wav"), "wb") as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
        sound.writeframes(b"".join(struct.pack("<h", int(8000 * ((n // 10) % 2 * 2 - 1))) for n in range(16000)))
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        quiet = {"check": True, "capture_output": True}
        subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15", "-pix_fmt", "yuv420p", "-c:v", "mpeg4",
                        str(root / "media" / "clip.mp4")], **quiet)
        subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=96x64:rate=4", str(root / "media" / "moving.gif")], **quiet)
    # Larger than the phone may download; sparse, so it costs no disk space.
    with open(root / "media" / "huge.mp4", "wb") as huge:
        huge.truncate(12 * 1024 * 1024)
    link = root / "outside"
    if not link.is_symlink():
        link.symlink_to(base.parent)
    return root


def git_fixture(base):
    """A repository with an upstream, one commit to push, a staged, a modified and a new file, and a worktree."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture",
               GIT_COMMITTER_EMAIL="fixture@example.invalid", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    root, remote = base / "repo", base / "remote.git"
    root.mkdir(exist_ok=True)
    def git(*args, cwd=root):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)
    git("init", "-q", "-b", "main")
    (root / "README.md").write_text("Fixture\n"); (root / "app.kt").write_text("fun main() {}\n")
    git("add", "."); git("commit", "-q", "-m", "Start the fixture app")
    git("init", "-q", "--bare", str(remote), cwd=base); git("remote", "add", "origin", str(remote)); git("push", "-q", "-u", "origin", "main")
    # A merged feature branch, a tag, an open branch and six ideas merged at once.
    git("switch", "-q", "-c", "feature/graph"); (root / "graph.kt").write_text("val lanes = 4\n"); git("add", "."); git("commit", "-q", "-m", "Draw the branch graph")
    git("switch", "-q", "main"); (root / "notes.md").write_text("Notes\n"); git("add", "notes.md"); git("commit", "-q", "-m", "Add release notes")
    git("merge", "-q", "--no-ff", "feature/graph", "-m", "Merge feature/graph"); git("tag", "v0.1")
    git("switch", "-q", "-c", "feature/wip"); (root / "wip.kt").write_text("// phone layout\n"); git("add", "."); git("commit", "-q", "-m", "Sketch the phone layout")
    for n in range(1, 7):
        git("switch", "-q", "-c", f"tip/{n}", "main"); (root / f"tip{n}.txt").write_text(f"{n}\n"); git("add", "."); git("commit", "-q", "-m", f"Try idea {n}")
    # An octopus merge keeps all six tips open at once, so the graph is wider than its third of a phone screen.
    git("switch", "-q", "-c", "integration", "main"); git("merge", "-q", "--no-ff", "-m", "Try every idea together", *[f"tip/{n}" for n in range(1, 7)])
    # A commit that only adds a binary file, which has no line counts.
    git("switch", "-q", "-c", "assets", "main"); (root / "icon.png").write_bytes(bytes(range(256)) * 4); git("add", "."); git("commit", "-q", "-m", "Add the app icon")
    git("switch", "-q", "main")
    (root / ".git" / "info" / "exclude").write_text(".worktrees/\n")
    git("worktree", "add", "-q", "-b", "feature/bar", str(root / ".worktrees" / "bar"))
    (root / "app.kt").write_text("fun main() { println() }\n"); (root / "staged.kt").write_text("val x = 1\n"); git("add", "staged.kt")
    (root / "scratch.txt").write_text("scratch\n")
    (root / ".env").write_text("FIXTURE_TOKEN=not-a-real-secret\n")  # The phone may list it but never read it.
    return str(root)

def fake_alert_filter(desktop, advertise):
    """A stand-in for the desktop's rem.notices filter, until the phone is tested against the real one.

    With advertise, it answers the filter contract from a script, one step per filtered read. Otherwise it plays an older
    desktop: the overview says nothing, and a read that sends the filter anyway gets a reply the phone's test looks for.
    """
    from datetime import datetime, timezone
    plain = desktop._read
    now = lambda: datetime.now(timezone.utc).isoformat()
    def item(identity, kind, reason, body):
        return {"id": identity, "kind": kind, "reason": reason, "body": body, "created_at": now(), "title": "Fixture secret title", "needs_user": reason == "needs_you"}
    # What each mark's read finds. Round 0 is from before the switches went on, and round 1 is named twice.
    script = {
        "": ("m1", [item("round-0", "proposals", "proposals", "Fixture old round")]),
        "m0": ("m1", [item("round-0", "proposals", "proposals", "Fixture old round")]),
        "m1": ("m2", [item("needs-1", "organization", "needs_you", "Fixture secret needs"), item("done-1", "task_update", "finished", "Fixture secret done"),
                      item("round-1", "proposals", "proposals", "Fixture secret round"), item("round-0", "proposals", "proposals", "Fixture old round")]),
        "m2": ("m3", [item("round-1", "proposals", "proposals", "Fixture secret round")]),
        "m3": ("m4", [item("quiet-1", "task_update", "finished", "Fixture quiet finish")]),
        "m4": ("m5", [item("words-1", "task_update", "needs_you", "Fixture words shown")]),
    }
    def read(peer, op, params, services=None):
        value = plain(peer, op, params, services)
        if op == "overview" and advertise:
            return {**value, "rem_notices_filter": True}
        if op != "rem.notices" or "include" not in params:
            return value
        if not advertise:
            return {**value, "notices": [item("leak-1", "reply", "", "Filter sent to an older desktop")]}
        mark = params.get("mark", "")
        # Waits for every switch, so the script doesn't depend on when a read lands between taps.
        if set(params["include"]) != {"needs_you", "finished", "proposals"}:
            return {**value, "mark": mark or "m0"}
        following, notices = script.get(mark, (mark, []))
        return {**value, "mark": following, "notices": notices}
    desktop._read = read


class FixtureRem:
    """In-memory Rem home for --desktop. Same methods as desktop RemHome; no provider runs."""
    def __init__(self, root):
        self.store = self
        self.root = str(pathlib.Path(root).resolve())
        # Where the desktop's real attachment code keeps files the phone attached in Rem's chat.
        self.data_dir = pathlib.Path(root, "rem-data")
        self.messages = [{"id": "hello", "role": "rem", "body": "Hi, I am Rem on your fixture desktop.", "created_at": "2026-10-27T09:00:00+00:00"}]
        if "--links" in sys.argv:
            # A tapped file link fetches the file without Rem; a web link opens the phone's browser.
            pathlib.Path(root, "Fixture report.md").write_text("# Fixture report\n\nNo failed runs.\n")
            # A 1x1 PNG opens in a photo viewer; nothing on the test emulator opens the .tar file.
            pathlib.Path(root, "fixture chart.png").write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f86f73fc3f0007420302"
                "dd657cf10000000049454e44ae426082"))
            pathlib.Path(root, "fixture data.tar").write_bytes(b"\x00\x01fixture")
            # Secret-looking files stay on the desktop even when Rem links them.
            pathlib.Path(root, "api-token.txt").write_text("synthetic-not-a-real-token")
            here = pathlib.Path(root).resolve()
            self.messages.append({"id": "links", "role": "rem", "created_at": "2026-10-27T09:00:30+00:00",
                "body": f"[the fixture report](<{here}/Fixture report.md>)\n\n[the chart](<{here}/fixture chart.png>)\n\n"
                        f"[the raw data](<{here}/fixture data.tar>)\n\n[a secret file](<{here}/api-token.txt>)\n\nDocs: https://example.com/docs"})
            # Full paths written as plain text (with spaces) and as code; the desktop finds and lists them.
            shots = pathlib.Path(here, "Rem shots"); shots.mkdir(exist_ok=True)
            for name in ("paused screen.png", "review screen.png"):
                (shots / name).write_bytes(pathlib.Path(root, "fixture chart.png").read_bytes())
            self.messages.append({"id": "paths", "role": "rem", "created_at": "2026-10-27T09:00:40+00:00",
                "body": f"Saved them here:\n\n{shots}/paused screen.png\n\n`{shots}/review screen.png`"})
        self.items = {"fixture-task": {"id": "fixture-task", "title": "Check failed workflow runs", "type": "maintain", "prompt": "Look for failed runs",
            "project_dir": self.root, "state": "enabled", "revision": 1, "schedule": {"kind": "weekly", "local_time": "09:00", "weekdays": ["monday"]},
            "acceptance_criteria": [{"description": "List every failure"}], "active_run": None, "last_run": None, "recent_runs": []}}
        self.threads = {"fixture-thread": ("Check failed workflow runs", "No failed runs this week.")}
        if "--retention" in sys.argv:
            # Two recurring tasks that ran, a one-time task that finished and a link to one the desktop already removed.
            def ran(identity, title, kind, finished, thread=None):
                run = {"id": identity + "-run", "state": "succeeded", "summary_short": "", "created_at": finished.replace(":05:", ":00:"),
                       "finished_at": finished, "thread_id": thread or identity + "-thread"}
                self.items[identity] = {"id": identity, "title": title, "type": "errand", "prompt": title, "project_dir": self.root,
                    "state": "enabled", "revision": 1, "created_at": "2026-10-20T09:00:00+00:00",
                    "schedule": {"kind": kind, "local_time": "09:00"} if kind != "once" else {"kind": "once"},
                    "acceptance_criteria": [], "active_run": None, "last_run": run, "recent_runs": [run]}
            ran("daily-older", "Summarize yesterday's commits", "daily", "2026-10-30T09:05:00+00:00")
            ran("once-done", "Rename the release branch", "once", "2026-10-31T10:05:00+00:00", "once-thread")
            ran("daily-recent", "Check dependency updates", "daily", "2026-10-31T12:05:00+00:00")
            self.threads["once-thread"] = ("Rename the release branch", "Renamed release to release-2026.")
            self.messages += [
                {"id": "once-started", "role": "rem", "body": "Done: I renamed the branch.", "created_at": "2026-10-31T10:06:00+00:00",
                 "work": {"task_id": "once-done", "run_id": "once-done-run", "thread_id": "once-thread", "title": "Rename the release branch",
                          "type": "errand", "state": "succeeded"}},
                {"id": "gone", "role": "rem", "body": "Done: I archived the logs.", "created_at": "2026-10-31T10:07:00+00:00",
                 "work": {"task_id": "gone-task", "run_id": "gone-run", "thread_id": "gone-thread", "title": "Archive old logs",
                          "type": "errand", "state": "succeeded"}}]
        if "--work-thread" in sys.argv:
            # A worker transcript shaped like real ones: a long instruction, hundreds of tool and
            # thinking records, a report, a follow-up still running, and a run whose thread is gone.
            runs = [{"id": "tidy-run-2", "state": "executing", "created_at": "2026-10-27T10:00:00+00:00", "thread_id": "long-thread"},
                    {"id": "tidy-run-1", "state": "succeeded", "summary_short": "Old run", "created_at": "2026-10-26T10:00:00+00:00",
                     "finished_at": "2026-10-26T10:20:00+00:00", "thread_id": "missing-thread"}]
            self.items["tidy-task"] = {"id": "tidy-task", "title": "Tidy the phone thread view", "type": "maintain", "prompt": "Tidy it",
                "project_dir": self.root, "state": "enabled", "revision": 1, "schedule": {"kind": "once"}, "acceptance_criteria": [],
                "active_run": runs[0], "last_run": runs[1], "recent_runs": runs}
            self.slow_threads = {"long-thread"}
        if "--tickets" in sys.argv:
            # A real global board with one of Rem's tickets, named in Rem's text and on a self-work card.
            from gofer.ui.rem_home_store import RemHomeStore
            from gofer.ui.rem_kanban import RemKanban, ticket_ref
            self.kanban = RemKanban(RemHomeStore(pathlib.Path(root, "rem-kanban")))
            ticket = self.kanban.create("user", {"title": "Fix the sync conflict on reconnect", "status": "todo"}, "fixture-ticket")["ticket"]
            key = f"REM-{ticket['number']}"
            self.messages.append({"id": "ticket-text", "role": "rem", "created_at": "2026-10-27T09:01:00+00:00",
                "body": f"{key} is next. REM-404 isn't a ticket, and `{key}` in code stays as written."})
            self.messages.append({"id": "ticket-card", "role": "rem", "created_at": "2026-10-27T09:02:00+00:00",
                "body": f"Started {key} from my To do while you were away.", "self_work": {"kind": "ticket", "batch": "fixture-pass"},
                "work": {"task_id": "fixture-task", "run_id": "ticket-run", "thread_id": "fixture-thread", "title": ticket["title"],
                         "type": "develop", "state": "queued", "self_work": "ticket", "tickets": [ticket_ref(ticket, "worked")]}})
        if "--ui-tour" in sys.argv:
            # One task in every state the task list shows: working, waiting, needs you, couldn't finish, stopped, paused, coming up and done.
            def tour(identity, title, kind, active=None, last=None, state="enabled", schedule=None, nxt=None, thread="tour-thread"):
                for run in (active, last):
                    if run:
                        run.setdefault("id", identity + "-" + run["state"]); run.setdefault("created_at", "2026-11-09T09:00:00+00:00")
                        run.setdefault("thread_id", thread)
                self.items[identity] = {"id": identity, "title": title, "type": kind, "prompt": "Tour task", "project_dir": self.root,
                    "state": state, "revision": 1, "schedule": schedule or {"kind": "weekly", "local_time": "09:00", "weekdays": ["monday"]},
                    "acceptance_criteria": [], "active_run": active, "last_run": last, "recent_runs": [r for r in (active, last) if r],
                    **({"next_occurrence_at": nxt} if nxt else {})}
            done = {"finished_at": "2026-11-09T08:30:00+00:00"}
            tour("tour-audit", "Audit the phone UI against the desktop", "develop", schedule={"kind": "once"},
                 active={"state": "executing", "phase_label": "Running the unit tests"})
            tour("tour-backup", "Check last night's backups", "maintain", schedule={"kind": "once"},
                 active={"state": "queued", "waiting_reason": "Waiting for a free slot"})
            tour("tour-branch", "Rename the release branch", "errand", schedule={"kind": "once"},
                 last={"state": "blocked", "needs_user": True, "summary_short": "Which branch should I rename: **release** or **stage**?", **done})
            tour("tour-import", "Import Firefox bookmarks", "develop", schedule={"kind": "once"},
                 last={"state": "failed", "needs_user": False, "summary_short": "Gradle ran out of disk space during the build.", **done})
            tour("tour-notes", "Tidy the release notes", "errand", last={"state": "cancelled", **done})
            tour("tour-calendar", "Sync calendar reminders", "reminder", state="paused")
            tour("tour-deps", "Weekly dependency report", "research", nxt="2026-11-16T09:00:00+00:00",
                 last={"state": "succeeded", "summary_short": "Two updates are safe to take.", **done})
            tour("tour-vector", "Compare vector search options", "research", last={"state": "succeeded", **done})
        if "--rich" in sys.argv:
            # A finished research task with a saved report, checks and sources, a task being checked, and Rem replying.
            report = pathlib.Path(root, "Vector search.html").resolve()
            report.write_text("<html><head><title>Vector search for Second Brain</title></head><body>Report</body></html>")
            link = f"[Vector search](<{report}>)"
            self.messages.append({"id": "rich-result", "role": "rem", "kind": "task_update", "created_at": "2026-10-27T09:00:40+00:00",
                "body": "SQLite full-text search plus a small vector index is plenty.\n\n" + link,
                "details": "| Option | Recall |\n|---|---|\n| SQLite | 0.91 |\n| Qdrant | 0.94 |",
                "suggestions": ["Set up the SQLite option", "Why not Qdrant?"],
                "meta": {"provider": "claude_code", "model": "claude-sonnet-5-5", "seconds": 6.4},
                "work": {"task_id": "rich-task", "run_id": "rich-run", "thread_id": "fixture-thread", "title": "Compare vector search", "type": "research", "state": "succeeded"},
                "result": {"title": "Compare vector search", "type": "research", "state": "succeeded", "thread_id": "fixture-thread",
                    "checks": {"passed": 3, "failed": 0, "total": 3},
                    "artifacts": [{"name": report.name, "title": "Vector search for Second Brain", "path": str(report), "link": link, "kind": "html", "size": report.stat().st_size}],
                    # --many-sources: eight sources, so previews queue behind the three the phone fetches at once.
                    "sources": [{"url": "https://example.com/fixture-guide", "title": "", "kind": "cited"}]
                        + ([{"url": f"https://example.com/fixture-source-{n}", "title": "", "kind": "cited"} for n in range(1, 8)]
                           if "--many-sources" in sys.argv else [])}})
            self.messages.append({"id": "rich-ack", "role": "rem", "created_at": "2026-10-27T09:00:50+00:00", "body": "Started the sync research.",
                "work": {"task_id": "rich-running", "run_id": "rich-running-run", "thread_id": "fixture-thread", "title": "Local-first sync", "type": "research", "state": "verifying"}})
            self.items["rich-running"] = {"id": "rich-running", "title": "Local-first sync", "type": "research", "prompt": "Research sync engines",
                "project_dir": self.root, "state": "enabled", "revision": 1, "schedule": {"kind": "once"}, "acceptance_criteria": [],
                "active_run": {"id": "rich-running-run", "state": "verifying", "created_at": "2026-10-27T09:00:50+00:00", "thread_id": "fixture-thread",
                    "verification_thread_id": "fixture-review", "phase_label": "Checking the work",
                    "criteria": [{"id": "a", "result": "pass"}, {"id": "b", "result": "unknown"}, {"id": "c", "result": "unknown"}]},
                "last_run": None, "recent_runs": []}
            # The finished task's review stopped, so the phone can offer Retry review and Run again.
            stopped = {"id": "rich-run", "state": "blocked", "created_at": "2026-10-27T09:00:10+00:00", "finished_at": "2026-10-27T09:00:40+00:00",
                       "thread_id": "fixture-thread", "verification_thread_id": "fixture-review", "review_retryable": True}
            self.items["rich-task"] = {"id": "rich-task", "title": "Compare vector search", "type": "research", "prompt": "Compare options",
                "project_dir": self.root, "state": "enabled", "revision": 1, "schedule": {"kind": "daily", "local_time": "09:00"},
                "acceptance_criteria": [], "active_run": None, "last_run": stopped, "recent_runs": [stopped]}
            self.draft_reads = 0
        if "--desktop-attachments" in sys.argv:
            # A message sent from the desktop window with a picture, a text file and a file since deleted, stored by the
            # desktop's real attachment code as the window stores them, for the phone to fetch with rem.attachment.
            from gofer.ui.chat_media import attachment_thread_dir, store_chat_attachments
            from gofer.ui.rem_home import REM_ATTACHMENT_THREAD
            import base64
            files = [("desk.png", "image/png", fixture_png()), ("notes.txt", "text/plain", b"Notes from the desktop window\n"),
                     ("gone.png", "image/png", fixture_png(40, 30))]
            stored = store_chat_attachments({"threadId": REM_ATTACHMENT_THREAD, "files": [
                {"name": n, "type": t, "data": base64.b64encode(d).decode()} for n, t, d in files]}, self.data_dir)["attachments"]
            (attachment_thread_dir(self.data_dir.resolve(), REM_ATTACHMENT_THREAD) / stored[2]["storageName"]).unlink()
            self.messages.append({"id": "desk-attach", "role": "user", "body": "Sent from the desktop window",
                                  "created_at": "2026-10-27T09:00:30+00:00", "attachments": stored})
        if "--search" in sys.argv:
            # A long history: the only "orchid budget" is near the end of an old, long reply, far before the newest
            # messages rem.home sends, so the phone must search on the desktop and page back to open it.
            filler = " ".join(f"Line {i} of an old fixture reply that wraps across the screen." for i in range(40))
            self.messages.append({"id": "orchid", "role": "rem", "created_at": "2026-10-20T09:00:00+00:00",
                                  "body": filler + " In the end, the orchid budget was approved for Friday."})
            self.messages += [{"id": f"later-{i}", "role": "user" if i % 2 else "rem", "created_at": f"2026-10-2{1 + i // 40}T10:{i % 60:02d}:00+00:00",
                               "body": f"Routine fixture note {i}"} for i in range(160)]
        self.calls = []
    def _search_store(self):
        """The desktop's real store holding these messages, so search and paging run the desktop's own code."""
        import json as store_json
        from gofer.ui.rem_home_store import RemHomeStore
        if getattr(self, "_searched", (None, -1))[1] != len(self.messages):
            store = RemHomeStore(pathlib.Path(self.root, "rem-search", str(len(self.messages))))
            with store.connect() as db:
                for message in self.messages:
                    db.execute("INSERT OR IGNORE INTO messages(id, body) VALUES(?, ?)", (message["id"], store_json.dumps(message)))
            self._searched = (store, len(self.messages))
        return self._searched[0]
    def search(self, query, before=None, limit=50):
        return self._search_store().search(query, before=before, limit=limit)
    def page(self, before, through=None, limit=40):
        return self._search_store().page(before, through=through, limit=limit)
    def conversation(self):
        if "--rich" in sys.argv:
            return {"conversation": {"turn_state": "replying", "provider": "claude_code", "turn_label": "Reading earlier messages",
                                     "draft_reply": "Here is what I found so f"}, "messages": self.messages}
        return {"conversation": {"turn_state": "idle", "provider": "codex"}, "messages": self.messages}
    def tasks(self, summary=False):
        # Desktop dev 6da259b asks for summaries; this stand-in returns the same tasks either way.
        self.advance_progress()
        return [t for t in self.items.values() if t["state"] != "deleted"]
    def start_progress(self, sent):
        """--task-progress: a one-time task the phone asked for moves through its steps; two other runs must stay off the phone's notification."""
        import time
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.progress_started = getattr(self, "progress_started", {})
        number = len(self.progress_started) + 1
        task, run = f"progress-task-{number}", f"progress-run-{number}"
        self.progress_started[task] = time.monotonic()
        def item(identity, title, active):
            return {"id": identity, "title": title, "type": "maintain", "prompt": "x", "project_dir": self.root, "state": "enabled", "revision": 1,
                "schedule": {"kind": "once"}, "acceptance_criteria": [], "active_run": active, "last_run": None, "recent_runs": []}
        def running(identity, state):
            return {"id": identity, "state": state, "created_at": now, "thread_id": "fixture-thread"}
        def work(task_id, run_id):
            return {"task_id": task_id, "run_id": run_id, "thread_id": "fixture-thread", "title": "Fixture secret task", "type": "maintain", "state": "queued"}
        self.items[task] = item(task, "Fixture secret task", running(run, "queued"))
        self.messages.append({"id": f"queued-{run}", "role": "rem", "body": "Started it.", "created_at": now, "work": work(task, run)})
        # Self-work posts nothing in Rem's chat; a task started from the desktop follows the desktop's own message.
        self.items.setdefault("self-work-task", item("self-work-task", "Fixture self-work", running("self-work-run", "executing")))
        if "desktop-task" not in self.items:
            self.items["desktop-task"] = item("desktop-task", "Fixture desktop task", running("desktop-run", "executing"))
            self.messages += [{"id": "desktop-asked", "role": "user", "body": "From the desktop", "created_at": now},
                              {"id": "queued-desktop-run", "role": "rem", "body": "Started it.", "created_at": now, "work": work("desktop-task", "desktop-run")}]
    def advance_progress(self):
        import time
        for task, started in getattr(self, "progress_started", {}).items():
            item, age = self.items[task], time.monotonic() - started
            if item["active_run"] is None:
                continue
            if age >= 90:
                item["last_run"] = {**item["active_run"], "state": "succeeded", "finished_at": "2026-11-09T12:00:00+00:00"}
                item["recent_runs"] = [item["last_run"]]
                item["active_run"] = None
            else:
                item["active_run"]["state"] = "queued" if age < 18 else "executing" if age < 36 else "verifying"
    def thread(self, identifier):
        if identifier == "tour-thread":
            return {"metadata": {"title": "Audit the phone UI against the desktop"}, "running": True, "messages": tour_transcript()}
        if identifier == "long-thread":
            if identifier in getattr(self, "slow_threads", ()):
                # The first read is slow, so the phone shows its loading state.
                self.slow_threads.discard(identifier)
                import time
                time.sleep(3)
            return {"metadata": {"title": "Tidy the phone thread view"}, "running": True, "messages": work_transcript()}
        title, body = self.threads[identifier]
        return {"metadata": {"title": title}, "running": False,
                "messages": [{"id": "w1", "role": "assistant", "body": body}]}
    def messages_after(self, after, limit=300):
        """Nothing new in Rem's chat for `rem.notices`; only the position answers."""
        latest = len(self.messages)
        return {"latest": latest, "last": latest, "messages": [], "asked": None, "sources": {}}

    def thread_ids(self):
        return list(self.threads)
    def mutate_run(self, identity, payload, action):
        self.calls.append(("run." + action, identity))
    def read(self):
        import contextlib
        return contextlib.nullcontext(None)
    def setting(self, db, key):
        if key == "conversation" and "--rich" in sys.argv:
            # The reply grows by a few words on every rem.draft read, like a reply being written.
            self.draft_reads += 1
            words = "Here is what I found so far about vector search for your notes and why SQLite is enough for now".split()
            return {"turn_state": "replying", "turn_label": "Writing the reply", "draft_reply": " ".join(words[: 4 + 2 * self.draft_reads])}
        return {}
    def retry_review(self, run, payload):
        self.calls.append(("retry_review", run))
        item = self.items["rich-task"]
        item["active_run"] = {**item["last_run"], "state": "verifying", "review_retryable": False, "phase_label": "Checking the work"}
        item["last_run"] = item["active_run"]
    def submit_message(self, payload, source=None):
        self.calls.append(("message", payload["body"]))
        if payload.get("attachments"):
            # Names, sizes and SHA-256 of the files the phone attached, as the desktop stored them.
            import hashlib
            from gofer.ui.chat_media import attachment_thread_dir
            from gofer.ui.rem_home import REM_ATTACHMENT_THREAD
            folder = attachment_thread_dir(self.data_dir.resolve(), REM_ATTACHMENT_THREAD)
            self.calls.append(("attachments", [[a["name"], a["size"], hashlib.sha256((folder / a["storageName"]).read_bytes()).hexdigest()]
                                               for a in payload["attachments"]]))
        reply = {"id": "reply-" + payload["request_id"], "role": "rem", "body": "Fixture Rem reply", "created_at": "2026-10-27T09:01:01+00:00",
                 "suggestions": ["Anything else running?"]}
        if "--media" in sys.argv:
            # A picture, a video and a file named like a picture that isn't one, offered through the desktop's real reply-file path.
            reply.update(reply_to=payload["request_id"], files=[{"name": p.name, "size": p.stat().st_size, "path": str(p)} for p in fixture_media(self.root)])
        elif "--sent-files" in sys.argv:
            # Files that aren't pictures or videos wait for a tap: a PDF that opens in a viewer and a .tar nothing opens.
            reply.update(reply_to=payload["request_id"], files=[{"name": p.name, "size": p.stat().st_size, "path": str(p)} for p in fixture_sent_files(self.root)])
        sent = {"id": payload["request_id"], "role": "user", "body": payload["body"], "created_at": "2026-10-27T09:01:00+00:00"}
        if payload.get("attachments"):
            sent["attachments"] = payload["attachments"]  # As the desktop's Rem keeps them with the message.
        self.messages += [sent, reply]
        if "--task-progress" in sys.argv and payload["body"].startswith("Start progress"):
            self.start_progress(sent)
    def instruct(self, thread, payload):
        self.calls.append(("instruct", payload["body"]))
    def create_task(self, payload):
        self.calls.append(("create", payload["title"]))
        self.items[payload["request_id"]] = {"id": payload["request_id"], "state": "enabled", "revision": 1, "acceptance_criteria": [],
            "active_run": None, "last_run": None, "recent_runs": [], **{k: v for k, v in payload.items() if k != "request_id"}}
    def edit_task(self, task, payload, action):
        self.calls.append(("task." + action, task))
        item = self.items[task]
        if action == "run":
            run = {"id": "fixture-run", "state": "succeeded", "summary_short": "No failures found", "finished_at": "2026-10-27T09:02:00+00:00", "thread_id": "fixture-thread"}
            item.update(last_run=run, recent_runs=[run])
        elif action == "pause":
            item["state"] = "paused"
        elif action == "delete":
            item["state"] = "deleted"
        elif payload.get("enabled"):
            item["state"] = "enabled"
        item["revision"] += 1

def fixture_png(width=480, height=320):
    """A small gradient PNG, made here so the fixture needs no image library."""
    import struct, zlib
    rows = b"".join(b"\0" + bytes(v for x in range(width) for v in (x * 255 // width, y * 255 // height, 160)) for y in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")

def fixture_media(root):
    folder = pathlib.Path(root, "media"); folder.mkdir(exist_ok=True)
    picture, video, broken = folder / "Fixture chart.png", folder / "Fixture demo.mp4", folder / "Not a picture.png"
    picture.write_bytes(fixture_png())
    video.write_bytes(pathlib.Path(__file__).resolve().parent.parent.joinpath("app/src/androidTest/assets/rem-sample.mp4").read_bytes())
    broken.write_bytes(b"This is plain text, not a PNG.\n")
    return [picture, video, broken]

def fixture_sent_files(root):
    folder = pathlib.Path(root, "sent"); folder.mkdir(exist_ok=True)
    pdf, tar = folder / "Fixture notes.pdf", folder / "Fixture data.tar"
    objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>", b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>"]
    body, offsets = b"%PDF-1.4\n", []
    for number, item in enumerate(objects, 1):
        offsets.append(len(body)); body += b"%d 0 obj\n" % number + item + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf.write_bytes(body + b"trailer\n<</Size 4/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % xref)
    tar.write_bytes(b"\x00\x01fixture")
    return [pdf, tar]

class TestSecrets:
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def put(self, key, value): self.values[key] = value

async def main():
    with tempfile.TemporaryDirectory(prefix="raticode-emulator-") as folder:
        registry = DeviceRegistry(pathlib.Path(folder), TestSecrets(), initialize=True)
        invitations = Invitations(registry)
        application = DeviceApplication(registry)
        listener = DeviceListener(registry, invitations, application)
        slow_lan = "--slow-lan" in sys.argv
        port = await listener.start("127.0.0.1", 0 if slow_lan else PORT)
        if slow_lan:
            backend_port = port
            async def proxy(reader, writer):
                upstream, outgoing = await asyncio.open_connection("127.0.0.1", backend_port)
                async def forward(source, destination, delayed=False):
                    try:
                        if delayed:
                            header = await source.readexactly(4)
                            body = await source.readexactly(int.from_bytes(header, "big"))
                            destination.write(header[:1]); await destination.drain()
                            await asyncio.sleep(1.3)
                            destination.write(header[1:] + body[:1]); await destination.drain()
                            await asyncio.sleep(1.3)
                            destination.write(body[1:]); await destination.drain()
                        while data := await source.read(4096):
                            destination.write(data); await destination.drain()
                    except (OSError, asyncio.IncompleteReadError):
                        pass
                    finally:
                        destination.close()
                await asyncio.gather(forward(reader, outgoing), forward(upstream, writer, True))
            proxy_server = await asyncio.start_server(proxy, "127.0.0.1", PORT)
            port = proxy_server.sockets[0].getsockname()[1]
        invitation = invitations.create("Disposable emulator desktop", {"host": "10.0.2.2", "port": port})
        pathlib.Path(OUT, "raticode-test-invitation.txt").write_text(invitation["uri"])
        application.lan_endpoint = {"host": "10.0.2.2", "port": port}
        if "--slow-files" in sys.argv:
            # A slow desktop or link: each uploaded 16 KiB chunk takes 150 ms, so an upload is still going when a test acts.
            import time
            receive = application.files.receive
            def slow_receive(*args, **kwargs):
                time.sleep(0.15)
                return receive(*args, **kwargs)
            application.files.receive = slow_receive
        async def response(**options):
            if "--parity" in sys.argv:
                await asyncio.sleep(7)
            if "--history" in sys.argv:
                yield {"type": "thought", "text": "**Reviewing the attachment**"}
                yield {"type": "thought", "text": "bash", "trace": {"id": "fixture-tool", "kind": "tool", "title": "bash", "category": "shell", "command": "printf fixture", "status": "completed", "output": "fixture"}}
            yield {"type": "final", "message": {"body": "Disposable fixture response"}}
        jobs, steering = ChatJobs(pathlib.Path(folder)), ChatSteering(pathlib.Path(folder))
        bridge = DeviceChatBridge(application, jobs, steering, pathlib.Path(folder), source=response)
        application.local_chat_jobs = jobs
        application.rem_ready = lambda: bridge.error is None
        asyncio.create_task(bridge.run())
        granted = set()
        window_at = 0
        # --browser: the window's browser tabs, and the pages phones sent that it "opened".
        browser_acks, browser_opened = [], []
        pending_deletes = {}
        read_state = {}
        grant_at = {}
        while True:
            for peer in registry.list():
                if peer["state"] == "pending": registry.confirm(peer["device_id"], peer["fingerprint"])
                if peer["state"] == "active" and peer["device_id"] not in granted:
                    deadline = grant_at.setdefault(peer["device_id"], asyncio.get_running_loop().time() + (15 if "--delay-grant" in sys.argv else 0))
                    if asyncio.get_running_loop().time() < deadline:
                        continue
                    if "--parity" in sys.argv:
                        from gofer.ui.device_context import shared_context
                        root = pathlib.Path(folder)
                        code = root / "code"; code.mkdir(exist_ok=True)
                        caps = [{"id": "codex", "defaultPermissionMode": "workspace-write"}, {"id": "grok", "defaultPermissionMode": "cli-managed"}, {"id": "antigravity", "defaultPermissionMode": "cli-managed"}]
                        application.workspace.enable(peer["device_id"], True)
                        application.workspace.providers = [{"id": p["id"], "default_permission": p["defaultPermissionMode"], "permission_modes": ["read-only", "workspace-write", "danger-full-access"] if p["id"] == "codex" else ["default", "cli-managed"], "models": ["fixture-a", "fixture-b"], "efforts": {"fixture-a": ["low", "high"], "fixture-b": ["low", "high"]}} for p in caps]
                        for group in ("active", "pinned", "archived"):
                            meta = {"id": "fixture-" + group, "title": group.capitalize() + " desktop thread", "projectRoot": folder, "projectName": "Notes", "provider": "codex", "model": "fixture-a", "mobileGroup": group, "pinned": group == "pinned", "archived": group == "archived", "updatedAt": "2026-09-21T14:20:00Z",
                                    # The active thread has a finished turn nobody opened yet, so desktop marks it unread.
                                    "unread": group == "active"}
                            workflow = {"remThreads": {"projects": [{"root": folder, "name": "Notes"}, {"root": str(code), "name": "Code"}]}}
                            context = shared_context(meta, workflow, caps, root)
                            messages = [{"id": "buried-" + group, "role": "assistant", "body": "The buried README search result"}] + [{"id": group + str(n), "role": "user", "body": "Recent message " + str(n)} for n in range(65)]
                            application.workspace.exchange(peer["device_id"], meta, messages, context, None)
                        if "--read-sync" in sys.argv:
                            # Desktop control makes the overview report thread_read, so the phone sends read state.
                            application.desktop.enable(peer["device_id"], True)
                    elif "--workspace" in sys.argv or "--history" in sys.argv:
                        # The same shared context the desktop server builds for a renderer thread.
                        from gofer.ui.device_context import shared_context
                        application.workspace.enable(peer["device_id"], True)
                        application.workspace.providers = [{"id": "codex", "models": ["fixture-a", "fixture-b"], "permission_modes": ["read-only", "workspace-write"], "default_permission": "read-only"}]
                        meta = {"id": "existing-renderer-thread", "title": "Desktop project chat", "projectRoot": folder, "projectName": "Fixture project",
                                "provider": "codex", "model": "fixture-a", "permissionsByProvider": {"codex": "read-only"}}
                        projects = [{"root": folder, "name": "Fixture project"}]
                        if "--git" in sys.argv:
                            # A disposable Git repository as a second open project, for the phone's source control view.
                            projects.append({"root": git_fixture(pathlib.Path(folder)), "name": "Fixture repo"})
                        if "--files" in sys.argv:
                            # Sample files of every kind for the phone's file explorer, inside the plain fixture project.
                            explorer_fixture(pathlib.Path(folder))
                        workflow = {"remThreads": {"projects": projects}}
                        context = shared_context(meta, workflow, [{"id": "codex", "defaultPermissionMode": "read-only"}], pathlib.Path(folder))
                        history = [{"id": "desktop-question", "role": "user", "body": "Check this fixture"}]
                        if "--history" in sys.argv:
                            history += [
                                {"id": "desktop-thought", "role": "assistant", "body": "**Prioritizing skill review**", "kind": "thought", "groupId": "desktop-turn"},
                                # Hidden context between thoughts: desktop leaves it out of phone history.
                                {"id": "desktop-memory", "role": "assistant", "body": "Compacted earlier conversation", "kind": "memory"},
                                {"id": "desktop-continuation", "role": "system", "body": "Continuation of the previous turn", "kind": "continuation-context"},
                                {"id": "desktop-tool-start", "role": "assistant", "body": "bash", "kind": "thought", "groupId": "desktop-turn", "trace": {"id": "desktop-tool", "kind": "tool", "title": "bash", "category": "shell", "command": "pwd", "status": "running"}},
                                {"id": "desktop-tool-result", "role": "assistant", "body": "bash", "kind": "thought", "groupId": "desktop-turn", "trace": {"id": "desktop-tool", "kind": "tool", "title": "Tool result", "output": "/fixture", "status": "completed"}},
                            ]
                        if "--links" in sys.argv:
                            (pathlib.Path(folder) / "notes").mkdir(exist_ok=True)
                            (pathlib.Path(folder) / "notes" / "thread notes.md").write_text("Thread notes\n")
                            history += [{"id": "desktop-link", "role": "assistant", "body": "[the thread notes](<notes/thread notes.md>)", "kind": "final"}]
                        history += [{"id": "desktop-answer", "role": "assistant", "body": "The desktop history is here.", "kind": "final"},
                                    {"id": "desktop-summary", "role": "assistant", "body": "", "kind": "turn-summary", "durationMs": 2100, "running": False}]
                        application.workspace.exchange(peer["device_id"], meta, history, context, None)
                        if "--remote" in sys.argv:
                            from gofer.core.approvals import ApprovalRequest, ApprovalStore
                            from gofer.ui.api import decide_workflow_approval_payload
                            data = pathlib.Path(folder) / "approvals"
                            ApprovalStore(data).create_or_update(ApprovalRequest(workflow_id="fixture-deploy", run_id="run1", node_id="gate", message="Deploy the fixture build?"))
                            application.remote.data_dir = data
                            application.remote.decide_workflow = decide_workflow_approval_payload
                            application.remote.swarms = FixtureSwarms(folder)
                            application.remote.enable(peer["device_id"], True)
                            if "--desktop" in sys.argv:
                                from gofer.ui.organizations import OrganizationManager
                                async def no_stream(**_):
                                    if False:
                                        yield {}
                                orgs = OrganizationManager(pathlib.Path(folder) / "orgs", stream=no_stream, start_runtime=False)
                                plain = {"name": "Fixture engineering", "projectRoots": [folder], "employees": [{"id": "lead", "name": "Fixture lead", "title": "CTO"}]}
                                try:
                                    org = orgs.call(folder, "user", {"action": "create", "params": {"config": {**plain, "mission": "Ship calm fixture releases",
                                        "employees": [{"id": "lead", "name": "Fixture lead", "title": "CTO", "persona": "Careful and brief", "responsibilities": ["Plans releases"]},
                                                      {"id": "builder", "name": "Fixture builder", "title": "Engineer", "reportsTo": "lead", "paused": True}]}}})
                                except Exception:  # A desktop from before the five-section view knows only the plain fields.
                                    org = orgs.call(folder, "user", {"action": "create", "params": {"config": plain}})
                                orgs.call("", "user", {"action": "task_create", "organizationId": org["id"], "params": {"title": "Ship the fixture release", "assignee": "lead", "approvalRequired": True}})
                                try:
                                    # The five sections' plan: a project with a milestone serving an organization milestone, an epic and a note.
                                    for kind, item in (("project", {"id": "fixture-app", "name": "Fixture app", "workspacePath": folder, "owner": "lead"}),
                                                       ("milestone", {"id": "fixture-beta", "title": "Fixture beta", "dueAt": "2026-12-01"}),
                                                       ("milestone", {"id": "fixture-logs", "title": "Fixture logs", "projectId": "fixture-app", "parentId": "fixture-beta"}),
                                                       ("epic", {"id": "fixture-epic", "title": "Fixture epic", "milestoneId": "fixture-logs", "projectId": "fixture-app", "owner": "lead"})):
                                        orgs.call("", "user", {"action": "plan_save", "organizationId": org["id"], "params": {"kind": kind, "item": item,
                                            "expectedRevision": orgs.store.get("", org["id"])["revision"]}})
                                    note = orgs.call("", "user", {"action": "knowledge_save", "organizationId": org["id"], "params": {"entry": {"kind": "finding",
                                        "title": "Fixture finding", "topic": "Fixture", "summary": "What the fixture learned", "body": "The fixture body text.", "projectId": "fixture-app"}}})
                                    orgs.call("", "user", {"action": "task_create", "organizationId": org["id"], "params": {"title": "Fixture epic ticket", "epicId": "fixture-epic",
                                        "project": "fixture-app", "assignee": "lead", "status": "backlog", "knowledge": [note["id"]]}})
                                except Exception as exc:
                                    print(f"Fixture organization has no plan on this desktop: {type(exc).__name__}", file=sys.stderr)
                                # The window's app log: a few entries at every level, then one more each second while the fixture runs.
                                import json as log_json
                                from datetime import datetime, timezone
                                app_log = pathlib.Path(folder) / "logs" / "app.jsonl"
                                app_log.parent.mkdir()
                                def log_line(level, message, source="backend"):
                                    with app_log.open("a", encoding="utf-8") as handle:
                                        handle.write(log_json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                                            "level": level, "source": source, "message": message}) + "\n")
                                log_line("info", "Starting Raticode fixture", "desktop")
                                log_line("debug", "Fixture debug detail")
                                log_line("warn", "Fixture phone request was slow", "devices")
                                log_line("error", "Fixture failure\nTraceback (most recent call last):\nValueError: token=fixture-secret")
                                application.desktop.app_log_path = str(app_log)
                                log_beats = 0
                                application.desktop.rem = FixtureRem(folder)
                                if "--alert-filter" in sys.argv or "--alert-filter-old" in sys.argv:
                                    fake_alert_filter(application.desktop, "--alert-filter" in sys.argv)
                                if "--rich" in sys.argv:
                                    # Previews come from a stand-in: the fixture never contacts real sites.
                                    application.desktop.fetch_preview = lambda url: {"url": url, "kind": "page", "title": "Fixture guide page",
                                        "description": "A page the fixture made up", "site_name": "Example", "image": ""}
                                application.desktop.organizations = orgs
                                application.desktop.usage = lambda: {"days": 30, "observed_at": "2026-10-27T09:00:00+00:00", "accounts": [{"provider": "codex", "label": "Codex",
                                    "status": "available", "reason": "", "windows": [{"label": "Weekly", "used_percent": 35, "resets_at": "2026-11-01T00:00:00+00:00"}]}],
                                    "activity": {"calls": 7, "input_tokens": 900, "output_tokens": 300, "providers": [{"provider": "codex", "calls": 7}]}}
                                window_settings = {"appearance": {"theme": "system"}, "assistant": {"provider": "codex", "model": "fixture-a"}, "editor": {"fontSize": 13},
                                                   "browser": {"engine": "raticode"}}
                                application.desktop.enable(peer["device_id"], True)
                                if "--workflows" in sys.argv:
                                    # Disposable Rattish workflows in the shared project. Steps only wait until stopped; no command runs.
                                    import anyio
                                    import gofer.ui.api as workflow_api
                                    from gofer.rattish.run_service import run_rattish_file
                                    from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry
                                    from gofer.rattish.workspaces import create_registered_workflow
                                    async def waiting_step(node, run_context, bindings):
                                        while not pathlib.Path(folder, "release-workflow").exists():
                                            await anyio.sleep(0.05)
                                        return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})
                                    async def fake_run(*args, **kwargs):
                                        return await run_rattish_file(*args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": waiting_step}))
                                    workflow_api.run_rattish_file = fake_run
                                    step = "  type: bash-command\n  command: fixture-only\n"
                                    sources = {"Fixture report": "Rattish: 1\nWorkflow:\n  name: Fixture report\n" + "Node collect:\n" + step + "  to:\n    - summarize\n    - check\n"
                                               + "Node summarize:\n" + step + "  to: publish\n" + "Node check:\n" + step + "  to: publish\n"
                                               + "Node publish:\n" + step + "  needs:\n    - summarize\n    - check\n",
                                               "Fixture digest": "Rattish: 1\nWorkflow:\n  name: Fixture digest\nNode gather:\n" + step}
                                    for name, source in sources.items():
                                        created = create_registered_workflow(pathlib.Path(folder), name, registry_dir=data)
                                        created.entrypoint.write_text(source, encoding="utf-8")
                    else:
                        application.authorize(peer["device_id"], str(uuid4()), {"title": "Fixture project", "project_id": str(uuid4()), "project_path": folder, "allow_thread_create": True, "provider": "codex", "model": "cli-default", "permission_mode": "read-only"})
                    granted.add(peer["device_id"])
                if "--threads" in sys.argv and peer["device_id"] in granted:
                    import json, time
                    for row in registry.db.execute("SELECT thread FROM device_workspace_threads WHERE peer=?", (peer["device_id"],)).fetchall():
                        exported = application.workspace.export(peer["device_id"], row[0])
                        if exported.get("mobile_created"):
                            pathlib.Path(OUT, "raticode-test-thread-home.json").write_text(json.dumps(exported))
                        if "--read-sync" in sys.argv and exported.get("metadata"):
                            # What the desktop now holds for each thread, for LiveThreadSelectionTest.
                            meta = exported["metadata"]
                            read_state[meta["title"]] = {"unread": meta.get("unread") is True, "archived": meta.get("archived") is True}
                            pathlib.Path(OUT, "raticode-test-thread-read.json").write_text(json.dumps(read_state))
                        if exported.get("delete_requested"):
                            pending_deletes.setdefault(row[0], time.monotonic())
                            if time.monotonic() - pending_deletes[row[0]] > 8:
                                application.workspace.remove(peer["device_id"], row[0])
                if "--desktop" in sys.argv and peer["device_id"] in granted and asyncio.get_running_loop().time() >= window_at:
                    # Stands in for the desktop window: publish settings and apply queued phone changes.
                    import json
                    window_at = asyncio.get_running_loop().time() + 1
                    applied = []
                    for change in application.desktop.exchange_settings(window_settings, [])["changes"]:
                        for key, value in change["values"].items():
                            section, field = key.split(".")
                            window_settings.setdefault(section, {})[field] = value
                        applied.append(change["id"])
                    if applied:
                        application.desktop.exchange_settings(window_settings, applied)
                    if "--browser" in sys.argv:
                        tabs = [{"id": "browser:docs", "title": "Fixture docs", "url": "https://example.com/", "active": True, "owner": "you"},
                                {"id": "browser:rem", "title": "Rem's research", "url": "https://example.org/", "owner": "rem", "label": "Rem"},
                                {"id": "browser:preview", "title": "Local preview", "url": "file:///tmp/report.html", "owner": "you"}]
                        engine = window_settings.get("browser", {}).get("engine", "raticode")
                        try:
                            opens = application.desktop.exchange_browser(tabs, "https://duckduckgo.com/?q={query}", browser_acks, engine)["opens"]
                        except TypeError:  # A desktop from before the engine field.
                            opens = application.desktop.exchange_browser(tabs, "https://duckduckgo.com/?q={query}", browser_acks)["opens"]
                        browser_acks = [page["id"] for page in opens]
                        browser_opened += [page["url"] for page in opens if page["url"] not in browser_opened]
                        pathlib.Path(OUT, "raticode-test-browser.json").write_text(json.dumps({"opened": browser_opened}))
                    log_beats += 1
                    log_line("info", f"Fixture heartbeat {log_beats}")
                    org_state = orgs.call("", "user", {"action": "read", "organizationId": org["id"], "params": {}})
                    pathlib.Path(OUT, "raticode-test-desktop.json").write_text(json.dumps({"settings": window_settings, "rem": application.desktop.rem.calls,
                        "organization": {"state": org_state["runtime"]["state"], "tasks": [{k: t[k] for k in ("title", "status", "approvedRevision", "planRevision")} for t in org_state["runtime"]["tasks"]],
                            "workers": {e["id"]: e.get("paused") is True for e in org_state["config"]["employees"]},
                            "notes": sorted(e["title"] for e in org_state["runtime"].get("knowledge", {}).values())}}))
                if "--history" in sys.argv and peer["device_id"] in granted:
                    from gofer.devices.workspace import wire_id
                    exported = application.workspace.export(peer["device_id"], wire_id("existing-renderer-thread"))
                    if any(m.get("attachments") for m in exported["messages"]):
                        import json
                        pathlib.Path(OUT, "raticode-test-history-desktop.json").write_text(json.dumps(exported["messages"]))
            await asyncio.sleep(.05)

asyncio.run(main())
