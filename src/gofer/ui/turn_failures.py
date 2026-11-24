"""Why a worker turn failed, in the words the failure message uses.

The provider chat (``chat``), Rem's runs (``rem_home``) and Usage › Verified work
(``rem_verified_work``) share these. The chat and ``rem_home`` build their messages from them;
Verified work finds the same phrases in a blocked ticket's status entry to name the cause. So a
wording change here changes both sides together and can't silently break the match.

Kept free of imports from the rest of Raticode so Verified work can read it without loading the
provider chat.
"""

from __future__ import annotations

# A turn stops when it shows no provider activity for the idle window, or when
# it reaches the absolute limit even while active (rem_home.RemHome._await_turn).
TURN_IDLE_SECONDS = 20 * 60
TURN_LIMIT_SECONDS = 2 * 60 * 60

# A clean exit whose protocol records hold no answer. Rem retries it like a dropped
# connection, so keep its wording in step with rem_home.TRANSIENT_ERROR.
NO_FINAL_ANSWER = "The provider ended its turn without a final answer."
# Output that ended inside a JSON record, as when the stream dropped mid-write. Rem
# retries it like a dropped connection; rem_home.TRANSIENT_ERROR is built from it. A
# complete line of bad JSON is not retried, since a CLI that writes one does it every time.
INCOMPLETE_JSON_RECORD = "Provider emitted an incomplete JSON record"
# The provider app exited with an error code and said nothing more (chat).
PROVIDER_EXITED = "Provider exited with"
# The provider app was ended by a signal (chat.provider_exit_message).
PROVIDER_SIGNALLED = "The provider was stopped by"
# A worker turn whose provider app ended with a stream or exit error (rem_home).
PROVIDER_FAILED = "The provider app stopped with an error:"
# Temporary provider failures that were still failing after every retry (rem_home).
PROVIDER_GAVE_UP = "The provider kept failing after"
# A usage limit still reached after every wait for it to reset (rem_home).
USAGE_LIMIT_GAVE_UP = "The provider's usage limit was still reached after"

# The provider app couldn't be launched (chat), or Rem's turn couldn't (chat_jobs).
CLI_START_FAILED = "Could not start"
CLI_MISSING = "CLI is not available on PATH"
TURN_START_FAILED = "Could not start Rem turn"
# A prompt longer than the system lets a program be launched with (rem_home).
PROMPT_TOO_LONG = "couldn't start because its prompt exceeded the launch limit"


def idle_stop(seconds: float | None = None) -> str:
    """How a turn stopped for silence says so: "showed no activity for 20 minutes"."""
    window = TURN_IDLE_SECONDS if seconds is None else seconds
    return f"showed no activity for {int(window // 60)} minutes"


def turn_limit_stop(seconds: float | None = None) -> str:
    """How a turn stopped at the limit says so: "reached Rem's 120-minute turn limit"."""
    limit = TURN_LIMIT_SECONDS if seconds is None else seconds
    return f"reached Rem's {int(limit // 60)}-minute turn limit"


def cli_start_failed(binary: str, error: object) -> str:
    return f"{CLI_START_FAILED} '{binary}' CLI: {error}"


def cli_missing(binary: str) -> str:
    return f"'{binary}' {CLI_MISSING}"


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(phrase.casefold() in folded for phrase in phrases)


def not_started(text: str) -> bool:
    """Whether a failure message says the provider app or Rem's turn couldn't start."""
    return _has(text, (f"{CLI_START_FAILED} '", CLI_MISSING, TURN_START_FAILED, PROMPT_TOO_LONG))


def failure_cause(text: str) -> str:
    """Which failure a blocked task's message names, or "" when it names none of them.

    Older messages that say none of these phrases stay unnamed, so they keep counting as
    other failures.
    """
    if _has(text, (USAGE_LIMIT_GAVE_UP,)):
        return "usage_limit"
    if _has(text, (idle_stop(),)):
        return "no_activity"
    if _has(text, (turn_limit_stop(),)):
        return "turn_limit"
    if not_started(text):
        return "not_started"
    if _has(
        text,
        (
            PROVIDER_FAILED,
            PROVIDER_GAVE_UP,
            PROVIDER_EXITED,
            PROVIDER_SIGNALLED,
            NO_FINAL_ANSWER,
            INCOMPLETE_JSON_RECORD,
        ),
    ):
        return "provider_error"
    return ""
