"""No-model fake-process output based on pinned Grok 37949780 CLI definitions."""

import threading

import pytest

from gofer.subscriptions.grok_identity import grok_build_version


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (None, None),
        ("root", "CLI family"),
        ("agent", "CLI family"),
        ("json", "invalid version JSON"),
        ("metadata", "incomplete version"),
        ("exit", "probe failed"),
        ("timeout", "timed out"),
    ],
)
async def test_grok_family_identity_requires_all_source_markers(monkeypatch, bad, message):
    calls = []

    async def run(command, **kwargs):
        assert kwargs["timeout"] == 10
        assert kwargs["max_output_bytes"] == 128 * 1024
        assert "HOME" not in kwargs["env"]
        calls.append(command)
        if bad == "exit":
            return 1, "", "private token in stderr"
        if bad == "timeout":
            raise TimeoutError("timed out")
        output = {
            ("--help",): "Grok Build TUI" if bad != "root" else "Community Grok chatbot",
            ("agent", "--help"): "--no-leader --plugin-dir stdio" if bad != "agent" else "stdio",
            ("version", "--json"): (
                "{broken"
                if bad == "json"
                else "{}"
                if bad == "metadata"
                else '{"currentVersion":"1.0.24","channel":"stable"}'
            ),
        }[tuple(command[1:])]
        return 0, output, ""

    monkeypatch.setattr("gofer.subscriptions.grok_identity.run_subprocess", run)
    if bad:
        with pytest.raises((ValueError, TimeoutError), match=message):
            await grok_build_version("/fake/grok")
    else:
        assert await grok_build_version("/fake/grok") == "1.0.24"
        assert calls == [
            ["/fake/grok", "--help"],
            ["/fake/grok", "agent", "--help"],
            ["/fake/grok", "version", "--json"],
        ]


async def test_cancelled_identity_probe_does_not_launch(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("Cancelled probe launched a process")

    monkeypatch.setattr("gofer.subscriptions.grok_identity.run_subprocess", unexpected)
    event = threading.Event()
    event.set()
    with pytest.raises(ValueError, match="cancelled"):
        await grok_build_version("/fake/grok", event)
