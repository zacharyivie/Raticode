"""Jev, TypeSafe's hosted yes/no and multiple-choice classifier.

One request asks one question about ``state`` and returns a label and a score. The
endpoint is fixed: workflow source can't change the host, so a workflow can't send the
key anywhere else. Redirects are never followed. Every call writes one usage row,
including failures, and calls share one in-process limit of four in flight and one
rate-limit cooldown.

The key is read through a callable, normally from the OS keychain
(``gofer.classifiers.jev_settings``). It is never put into an environment, an error
message, a log line or a usage row.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

import anyio

from gofer.core.http import HttpClient, HttpRequest, HttpResponse, UrllibHttpClient

JEV_API_BASE = "https://api.typesafe.ai/v1"
JEV_ENDPOINT = f"{JEV_API_BASE}/systemone"
JEV_MODELS_ENDPOINT = f"{JEV_API_BASE}/models"
JEV_DOCS_URL = "https://docs.typesafe.ai/api"
DEFAULT_JEV_MODEL = "jev-1.14.0"
DEFAULT_JEV_THRESHOLD = 0.5
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_IN_FLIGHT = 4
MIN_COOLDOWN_SECONDS = 60.0
MAX_COOLDOWN_SECONDS = 300.0
MAX_STATE_BYTES = 512 * 1024
USAGE_PROVIDER = "jev"
LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"
# The single question each request asks. Choice option keys never collide with it.
QUESTION_KEY = "question"

QuestionType = Literal["yes-no", "choice"]
JevErrorKind = Literal[
    "not_configured",
    "auth",
    "rejected",
    "model_unavailable",
    "rate_limited",
    "unavailable",
    "network",
    "timeout",
    "invalid_response",
    "cancelled",
]
_RETRYABLE: frozenset[str] = frozenset({"rate_limited", "unavailable", "network", "timeout"})


class JevError(Exception):
    """A Jev failure with a message that is safe to show and store."""

    def __init__(
        self,
        kind: JevErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: JevErrorKind = kind
        self.message = message
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.retryable = kind in _RETRYABLE if retryable is None else retryable


@dataclass(frozen=True)
class JevQuestion:
    type: QuestionType
    instructions: str
    criteria_yes: str | None = None
    criteria_no: str | None = None
    options: Mapping[str, str] | None = None


@dataclass(frozen=True)
class JevAnswer:
    label: str
    score: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class JevResult:
    model: str
    requested_model: str
    answer: JevAnswer
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class JevLimiter:
    """At most ``limit`` calls in flight and one shared cooldown, across event loops.

    Workflow runs, key checks and CLI calls can each run on their own event loop, so the
    limiter uses thread primitives and polls instead of an anyio semaphore.
    """

    def __init__(
        self, limit: int = MAX_IN_FLIGHT, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._slots = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._clock = clock

    def cooldown_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._cooldown_until - self._clock())

    def start_cooldown(self, seconds: float) -> float:
        seconds = min(MAX_COOLDOWN_SECONDS, max(MIN_COOLDOWN_SECONDS, seconds))
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)
        return seconds

    async def acquire(self, cancel_event: threading.Event | None) -> None:
        while not self._slots.acquire(blocking=False):
            if cancel_event is not None and cancel_event.is_set():
                raise JevError("cancelled", "The Jev call was stopped.")
            await anyio.sleep(0.02)

    def release(self) -> None:
        self._slots.release()


SHARED_LIMITER = JevLimiter()


def build_request(*, model: str, state: Any, question: JevQuestion) -> dict[str, Any]:
    """The request body. ``state`` is the content; the question is about it."""
    body: dict[str, Any] = {"type": "noul", "instructions": question.instructions}
    if question.type == "yes-no":
        criteria = {
            key: value
            for key, value in (("true", question.criteria_yes), ("false", question.criteria_no))
            if value
        }
        if criteria:
            body["criteria"] = criteria
    else:
        # A choice question's options are its criteria: option name -> description.
        body = {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": dict(question.options or {}),
        }
    return {"model": model, "state": state, "questions": {QUESTION_KEY: body}}


def validate_state(state: Any) -> Any:
    """Reject an empty or oversized state before anything is sent."""
    if state is None or (isinstance(state, str) and not state.strip()):
        raise ValueError("Jev needs some text to classify, but the state is empty.")
    if isinstance(state, (dict, list)) and not state:
        raise ValueError("Jev needs some text to classify, but the state is empty.")
    if not isinstance(state, (str, dict, list)):
        state = json.dumps(state)
    size = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
    if size > MAX_STATE_BYTES:
        raise ValueError(
            f"The state is {size:,} bytes; Jev nodes send at most {MAX_STATE_BYTES:,}."
        )
    return state


def validate_question(question: JevQuestion, threshold: float) -> None:
    if not question.instructions.strip():
        raise ValueError("A Jev question can't be empty.")
    if question.type == "choice":
        options = question.options or {}
        if not 2 <= len(options) <= 255:
            raise ValueError("A Jev choice question needs 2 to 255 options.")
    elif question.type != "yes-no":
        raise ValueError(f"Unknown Jev question type {question.type!r}.")
    if not (isinstance(threshold, (int, float)) and 0 <= threshold <= 1):
        raise ValueError("The Jev threshold must be between 0 and 1.")


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevError("invalid_response", "Jev returned a score that isn't a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        raise JevError("invalid_response", "Jev returned a score outside 0 to 1.")
    return number


def parse_answer(payload: Any, question: JevQuestion, threshold: float) -> tuple[str, JevAnswer]:
    """Return the answering model and the validated answer. Never guesses a 'no'."""
    if not isinstance(payload, Mapping):
        raise JevError("invalid_response", "Jev returned a response that isn't a JSON object.")
    model = payload.get("model")
    answers = payload.get("answers")
    if not isinstance(model, str) or not model or not isinstance(answers, Mapping):
        raise JevError("invalid_response", "Jev's response has no model or answers.")
    answer = answers.get(QUESTION_KEY)
    if not isinstance(answer, Mapping):
        raise JevError("invalid_response", "Jev's response is missing the answer.")
    if question.type == "yes-no":
        if answer.get("type") != "noul":
            raise JevError("invalid_response", "Jev answered a different question type.")
        yes = _probability(answer.get("noul"))
        return model, JevAnswer(
            "yes" if yes >= threshold else "no", yes, {"yes": yes, "no": 1.0 - yes}
        )
    options = question.options or {}
    if answer.get("type") != "choice":
        raise JevError("invalid_response", "Jev answered a different question type.")
    label = answer.get("choice")
    if not isinstance(label, str) or label not in options:
        raise JevError("invalid_response", "Jev chose an option the node doesn't list.")
    confidence = _probability(answer.get("confidence"))
    raw = answer.get("probabilities")
    if not isinstance(raw, Mapping) or any(key not in options for key in raw):
        raise JevError("invalid_response", "Jev returned probabilities for unknown options.")
    probabilities = {str(key): _probability(value) for key, value in raw.items()}
    return model, JevAnswer(label, confidence, probabilities)


def _tokens(payload: Any) -> tuple[int | None, int | None]:
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    if not isinstance(usage, Mapping):
        return None, None

    def count(name: str) -> int | None:
        value = usage.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    return count("input_tokens"), count("output_tokens")


def retry_after_seconds(headers: Mapping[str, str], *, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header, given as seconds or an HTTP date."""
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def _rejection_fields(body: bytes) -> list[str]:
    """Field locations from a 400/422 body. The rest can echo the state, so it's dropped."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    fields: list[str] = []
    for item in detail if isinstance(detail, list) else []:
        location = item.get("loc") if isinstance(item, Mapping) else None
        if isinstance(location, list):
            parts = [str(part) for part in location if isinstance(part, (str, int))]
            if parts:
                fields.append(".".join(parts)[:120])
    return fields[:5]


def error_for_response(
    response: HttpResponse, model: str | None, limiter: JevLimiter
) -> JevError | None:
    status = response.status
    if 200 <= status < 300:
        return None
    if 300 <= status < 400:
        return JevError(
            "unavailable",
            f"Jev answered with a redirect (HTTP {status}), which Raticode doesn't follow.",
            status=status,
            retryable=False,
        )
    if status in {401, 403}:
        return JevError(
            "auth",
            "Jev rejected the key. Check it in Settings > Providers > Jev.",
            status=status,
        )
    if status == 404:
        named = f" {model!r}" if model else ""
        return JevError(
            "model_unavailable",
            f"Jev doesn't offer the model{named}. Use Check key in Settings > Providers > Jev "
            "to refresh the model list.",
            status=status,
        )
    if status in {400, 422}:
        fields = _rejection_fields(response.body)
        where = f" Check: {', '.join(fields)}." if fields else ""
        return JevError(
            "rejected", f"Jev rejected the request (HTTP {status}).{where}", status=status
        )
    if status in {429, 529}:
        wait = limiter.start_cooldown(retry_after_seconds(response.headers) or MIN_COOLDOWN_SECONDS)
        return JevError(
            "rate_limited",
            f"Jev is rate limiting this key. Raticode waits {round(wait)} seconds before "
            "calling it again.",
            status=status,
            retry_after_seconds=wait,
        )
    if status >= 500:
        return JevError("unavailable", f"Jev is unavailable (HTTP {status}).", status=status)
    return JevError("rejected", f"Jev returned HTTP {status}.", status=status, retryable=False)


async def _send(
    http: HttpClient, request: HttpRequest, cancel_event: threading.Event | None
) -> HttpResponse:
    """Send once. Stop pressed closes the connection; a timeout is a timeout."""
    try:
        if cancel_event is None:
            with anyio.fail_after(request.timeout_seconds):
                return await http.send(request)
        response: HttpResponse | None = None
        error: BaseException | None = None

        async def send() -> None:
            nonlocal response, error
            try:
                with anyio.fail_after(request.timeout_seconds):
                    response = await http.send(request)
            except Exception as exc:  # noqa: BLE001 - re-raised below
                error = exc
            finally:
                group.cancel_scope.cancel()

        async with anyio.create_task_group() as group:
            if not cancel_event.is_set():
                group.start_soon(send)
                while not cancel_event.is_set():
                    await anyio.sleep(0.05)
            group.cancel_scope.cancel()
        if cancel_event.is_set():
            raise JevError("cancelled", "The Jev call was stopped.")
        if error is not None:
            raise error
        assert response is not None
        return response
    except JevError:
        raise
    except TimeoutError:
        raise JevError(
            "timeout", f"Jev didn't answer within {request.timeout_seconds:g} seconds."
        ) from None
    except Exception as exc:  # noqa: BLE001 - the type is enough; text may hold the URL
        raise JevError("network", f"Couldn't reach Jev ({type(exc).__name__}).") from None


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JevError("invalid_response", "Jev returned a response that isn't JSON.") from None


@dataclass
class JevClient:
    """Calls Jev with a key read on demand. Inject ``http`` in tests; never the URL."""

    key: Callable[[], str | None]
    http: HttpClient = field(default_factory=UrllibHttpClient)
    data_dir: Path | None = None
    limiter: JevLimiter = field(default_factory=lambda: SHARED_LIMITER)
    record_usage: bool = True

    async def classify(
        self,
        *,
        model: str,
        state: Any,
        question: JevQuestion,
        threshold: float = DEFAULT_JEV_THRESHOLD,
        long_context: bool = False,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        cancel_event: threading.Event | None = None,
        run_id: str | None = None,
    ) -> JevResult:
        """Ask one question. Raises ``JevError``; writes exactly one usage row either way."""
        validate_question(question, threshold)
        state = validate_state(state)
        body = json.dumps(build_request(model=model, state=state, question=question)).encode(
            "utf-8"
        )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if long_context:
            headers[LONG_CONTEXT_HEADER] = "true"
        started = time.monotonic()
        status = "failed"
        answered_model: str | None = None
        tokens: tuple[int | None, int | None] = (None, None)
        try:
            response = await self._call(
                "POST", JEV_ENDPOINT, headers, body, timeout, cancel_event, model
            )
            payload = _json(response.body)
            tokens = _tokens(payload)
            answered_model, answer = parse_answer(payload, question, threshold)
            status = "completed"
            return JevResult(
                answered_model,
                model,
                answer,
                tokens[0],
                tokens[1],
                round((time.monotonic() - started) * 1000),
            )
        except JevError as exc:
            if exc.kind == "cancelled":
                status = "cancelled"
            raise
        except anyio.get_cancelled_exc_class():
            status = "cancelled"
            raise
        finally:
            if cancel_event is not None and cancel_event.is_set():
                status = "cancelled"
            self._record(answered_model or model, tokens, status, run_id)

    async def list_models(self, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> list[str]:
        """Model IDs the key can use. Also the key check; one usage row like any call."""
        status = "failed"
        try:
            response = await self._call(
                "GET",
                JEV_MODELS_ENDPOINT,
                {"Accept": "application/json"},
                None,
                timeout,
                None,
                None,
            )
            models = _model_ids(_json(response.body))
            status = "completed"
            return models
        finally:
            self._record(None, (None, None), status, None)

    async def _call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
        cancel_event: threading.Event | None,
        model: str | None,
    ) -> HttpResponse:
        remaining = self.limiter.cooldown_remaining()
        if remaining > 0:
            raise JevError(
                "rate_limited",
                f"Jev is rate limiting this key. Try again in {math.ceil(remaining)} seconds.",
                retry_after_seconds=remaining,
            )
        key = self.key()
        if not key:
            raise JevError("not_configured", "Add a Jev key in Settings > Providers > Jev first.")
        if cancel_event is not None and cancel_event.is_set():
            raise JevError("cancelled", "The Jev call was stopped.")
        await self.limiter.acquire(cancel_event)
        try:
            request = HttpRequest(
                method, url, {**headers, "Authorization": f"Bearer {key}"}, body, timeout
            )
            response = await _send(self.http, request, cancel_event)
        finally:
            self.limiter.release()
        error = error_for_response(response, model, self.limiter)
        if error is not None:
            raise error
        return response

    def _record(
        self,
        model: str | None,
        tokens: tuple[int | None, int | None],
        status: str,
        run_id: str | None,
    ) -> None:
        if not self.record_usage:
            return
        from gofer.core.usage_ledger import record_invocation

        metadata = {
            name: value
            for name, value in (("input_tokens", tokens[0]), ("output_tokens", tokens[1]))
            if value is not None
        }
        try:
            record_invocation(
                invocation_id=uuid.uuid4().hex,
                provider=USAGE_PROVIDER,
                model=model,
                metadata=metadata,
                status=status,
                data_dir=self.data_dir,
                run_id=run_id,
            )
        except Exception:  # noqa: BLE001 - accounting never fails the call
            return


def _model_ids(payload: Any) -> list[str]:
    """Names from ``{"models": [{"name": ...}]}``; a bare list is accepted too.

    TypeSafe lists aliases such as ``jev-latest``. Versioned IDs such as ``jev-1.14.0``
    are accepted whether or not they appear, so the list is a hint, never a gate.
    """
    items: Any = payload.get("models") if isinstance(payload, Mapping) else payload
    if not isinstance(items, list):
        raise JevError("invalid_response", "Jev's model list isn't a list.")
    models: list[str] = []
    for item in items:
        identifier = item.get("name", item.get("id")) if isinstance(item, Mapping) else item
        if isinstance(identifier, str) and identifier and identifier not in models:
            models.append(identifier)
    return models
