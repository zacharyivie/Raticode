"""Grok accounts: a provider account adapter (provider_accounts.py) for the grok CLI.

Each account is a ``grok`` provider profile with an ``account_id``. Its folder,
``<data>/grok-accounts/<account_id>``, is the ``GROK_HOME`` Grok runs with: the
``grok agent --no-leader stdio`` turns, Rem's replies and the ``_x.ai/billing``
usage reader. Grok keeps that account's sign-in there (``auth.json``) and refreshes
it itself; it also picks up a changed ``auth.json`` without a restart.

Raticode never reads, copies, stores or sends a token or ``auth.json``. It only
starts the unmodified ``grok`` with the folder set and the other sign-in variables
removed: ``grok login`` (or ``grok login --device-auth``) to sign in, ``grok logout``
before removing, and ``_x.ai/billing`` over Grok's ACP connection, which answers
only when the folder is signed in. Grok reports no email or account ID there, so
two folders signed in to the same Grok account aren't flagged.

Grok has no sandbox of its own in Raticode's runs. On Linux every Grok process
Raticode starts runs inside ``credential_mask``, which covers the whole
``grok-accounts`` folder and gives back only the turn's own account folder, with
the files in it that start programs read-only. See the website's Providers page
("What Grok's shell can reach").
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from gofer.core import provider_accounts as _accounts
from gofer.core.provider_accounts import (
    NEEDS_SIGN_IN,
    SIGNED_IN,
    STATUS_TIMEOUT_SECONDS,
    AccountAdapter,
)

PROVIDER = "grok"
ACCOUNTS_DIR = "grok-accounts"
# Environment that would make Grok use something other than the folder's sign-in:
# the API key (and its older name), a token handed over directly, and a path that
# moves auth.json out of GROK_HOME, which would make every account share one file.
OTHER_AUTH_ENV = ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY", "GROK_AUTH", "GROK_AUTH_PATH")
# The ACP command Raticode runs Grok with; --no-leader keeps accounts from sharing
# one background leader process.
ACP_ARGS = ("agent", "--no-leader", "stdio")
# Grok's JSON-RPC error for a request that needs a sign-in ("Authentication required").
AUTH_REQUIRED_CODE = -32000
# Shown when xAI refuses the device sign-in ("Device-code login is not available for
# this deployment"); a phone can't turn it on, so it points to the computer's sign-in.
DEVICE_SIGN_IN_HELP = (
    "Sign this account in on your computer instead: Settings › Providers › Grok, "
    "then Sign in on the account."
)
# Grok waits for the code at least this long (MIN_DEVICE_CODE_EXPIRY_FALLBACK_SECS in
# xai-grok-login's device_code.rs), longer only when xAI's own expires_in is.
DEVICE_SIGN_IN_SECONDS = 10 * 60
# Grok is installed but couldn't say, such as when it can't reach xAI. Not "not found"
# (UNAVAILABLE) and not signed out: the screens show "Unknown" or "Sign-in not checked".
UNKNOWN = "unknown"


def _grok_executable() -> str | None:
    from gofer.core.provider_capabilities import resolve_provider_executable

    return resolve_provider_executable("grok")


# --- status ------------------------------------------------------------------------------


def parse_billing_answer(answer: Any) -> dict[str, Any]:
    """The sign-in state from Grok's answer to ``_x.ai/billing``.

    A result means the folder is signed in. Grok's "Authentication required" error
    means it isn't. Anything else (no answer, another error) can't tell.
    """
    if not isinstance(answer, dict):
        return {"state": UNKNOWN, "reason": "Grok gave no sign-in status."}
    if "result" in answer:
        return {"state": SIGNED_IN, "plan": None, "email": None, "identity": None}
    error = answer.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        if error.get("code") == AUTH_REQUIRED_CODE or "authentic" in message.lower():
            return {
                "state": NEEDS_SIGN_IN,
                "plan": None,
                "email": None,
                "identity": None,
                "reason": "Not signed in yet.",
            }
        return {
            "state": UNKNOWN,
            "reason": f"Grok couldn't report this account: {message[:200] or 'no reason given'}.",
        }
    return {"state": UNKNOWN, "reason": "Grok gave no sign-in status."}


def _billing_answer(
    command: list[str], env: dict[str, str], folder: Path, *, timeout: float
) -> dict[str, Any] | None:
    """Start Grok's ACP agent, ask ``_x.ai/billing`` and return its JSON-RPC answer, or None."""
    process = subprocess.Popen(
        command,
        env=env,
        cwd=str(folder),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    lines: queue.Queue[bytes | None] = queue.Queue()

    def read() -> None:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, b""):
            if len(line) > 4 * 1024 * 1024:
                continue
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(payload: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps({"jsonrpc": "2.0", **payload}) + "\n").encode())
        process.stdin.flush()

    def answer(request_id: int) -> dict[str, Any] | None:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id and "method" not in message:
                return message
            if "method" in message and message.get("id") is not None:
                # Grok asked the client something; this check offers nothing.
                send(
                    {
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Not supported"},
                    }
                )

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }
        )
        if answer(1) is None:
            return None
        send({"id": 2, "method": "_x.ai/billing", "params": {}})
        return answer(2)
    except (OSError, ValueError):
        return None
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def read_grok_status(
    executable: str, env: dict[str, str], folder: Path, *, timeout: float = STATUS_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """The account's sign-in state: whether Grok's ``_x.ai/billing`` answers for the folder.

    The check runs inside the credential mask like a turn, keeping only this account's
    folder, so whatever Grok starts from the folder can't read the other accounts.
    """
    from gofer.core.credential_mask import masked_command

    command = masked_command([executable, *ACP_ARGS], data_dir=folder.parent.parent, keep=[folder])
    return parse_billing_answer(_billing_answer(command, env, folder, timeout=timeout))


def _read_status(output: str) -> dict[str, Any]:
    # Only used if something runs status_args directly; Grok reads it over ACP.
    return parse_billing_answer(_accounts.read_json_status(output))


async def _read_usage(
    executable: str, env: dict[str, str], _data_dir: Path | None
) -> list[dict[str, Any]] | None:
    from gofer.core.provider_usage import grok_account_allowance

    return await grok_account_allowance(executable, env)


ADAPTER = _accounts.register_adapter(
    AccountAdapter(
        provider=PROVIDER,
        label="Grok",
        cli_name="grok",
        folder_env="GROK_HOME",
        folder_name=ACCOUNTS_DIR,
        sign_in_args=("login",),
        device_sign_in_args=("login", "--device-auth"),
        sign_out_args=("logout",),
        status_args=ACP_ARGS,
        read_status=_read_status,
        # Looked up when called, so tests can stand in for the grok CLI.
        status_reader=lambda executable, env, folder: read_grok_status(executable, env, folder),
        read_usage=_read_usage,
        usage_source="grok _x.ai/billing",
        other_auth_env=OTHER_AUTH_ENV,
        identity_noun="Grok account",
        executable=lambda: _grok_executable(),
        sign_in_output=True,
        # grok 1.0.89 prints, on stderr, "To sign in, open this URL in your browser:",
        # the link indented on its own line, then "Then enter this code:" (or "Confirm
        # this code in your browser:" when the link already holds it) and the code
        # indented on its own line. It exits 0 once the code is approved. Its own
        # attempt to open the link goes to SignIns' no-op $BROWSER.
        phone_sign_in=True,
        device_sign_in_help=DEVICE_SIGN_IN_HELP,
        device_sign_in_seconds=DEVICE_SIGN_IN_SECONDS,
    )
)


def account_home(name: str, data_dir: Path | None = None) -> Path:
    """The GROK_HOME of the Grok account named ``name``."""
    return _accounts.account_config_dir(_accounts.get_account(name, data_dir, PROVIDER), data_dir)
