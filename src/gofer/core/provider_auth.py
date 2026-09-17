"""Bounded browser login sessions using the provider's own credential storage."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Any, cast

from gofer.core.provider_capabilities import (
    BROWSER_LOGIN_COMMANDS,
    ProviderId,
    resolve_provider_executable,
)
from gofer.utils.process import env_with_executable_on_path

LOGIN_TIMEOUT_SECONDS = 300


class ProviderAuthSessions:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._states: dict[str, dict[str, str]] = {}

    def start(self, provider: str) -> dict[str, str]:
        if provider not in BROWSER_LOGIN_COMMANDS:
            raise ValueError("This provider requires authentication through its own CLI setup.")
        with self._lock:
            if provider in self._processes:
                return self.status(provider)
            executable = resolve_provider_executable(cast(ProviderId, provider))
            if not executable:
                raise ValueError("Install and enable this provider before signing in.")
            env = {**os.environ, **env_with_executable_on_path(executable)}
            env.pop("NO_OPEN_BROWSER", None)
            process = subprocess.Popen(
                [executable, *BROWSER_LOGIN_COMMANDS[provider]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=os.name != "nt",
            )
            self._processes[provider] = process
            self._states[provider] = {"status": "pending"}
            threading.Thread(target=self._wait, args=(provider, process), daemon=True).start()
            return self.status(provider)

    def status(self, provider: str) -> dict[str, str]:
        with self._lock:
            return dict(self._states.get(provider, {"status": "idle"}))

    def _wait(self, provider: str, process: subprocess.Popen[bytes]) -> None:
        try:
            code = process.wait(timeout=LOGIN_TIMEOUT_SECONDS)
            state = (
                {"status": "complete"}
                if code == 0
                else {"status": "error", "error": "Sign-in did not complete. Please try again."}
            )
        except subprocess.TimeoutExpired:
            self._stop(process)
            state = {"status": "error", "error": "Sign-in timed out. Please try again."}
        with self._lock:
            if self._processes.get(provider) is process:
                self._states[provider] = state
                del self._processes[provider]

    @staticmethod
    def _stop(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait()

    def cancel(self, provider: str) -> dict[str, str]:
        with self._lock:
            process = self._processes.pop(provider, None)
            if process:
                self._stop(process)
            self._states[provider] = {"status": "idle"}
            return self.status(provider)

    def close(self) -> None:
        with self._lock:
            for provider in list(self._processes):
                self.cancel(provider)
