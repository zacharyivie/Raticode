"""Jev classifier client, keychain settings, routes and CLI. Uses a fake Jev only.

No test reaches api.typesafe.ai or reads a real key: every client gets a fake HttpClient,
and the keychain is replaced with an in-memory store.
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.classifiers import jev_settings
from gofer.classifiers.jev import (
    DEFAULT_JEV_MODEL,
    JEV_ENDPOINT,
    JEV_MODELS_ENDPOINT,
    LONG_CONTEXT_HEADER,
    MAX_STATE_BYTES,
    JevClient,
    JevError,
    JevLimiter,
    JevQuestion,
    build_request,
)
from gofer.core.http import HttpRequest, HttpResponse, retry_after_seconds
from gofer.core.usage_history import resolve_range, usage_history
from gofer.core.usage_ledger import usage_db

FAKE_KEY = "fake-jev-key-for-tests"
YES_NO = JevQuestion(
    "yes-no",
    "Does the writer ask for a refund?",
    criteria_yes="Asks for money back.",
    criteria_no="Anything else.",
)
CHOICE = JevQuestion(
    "choice",
    "Which team?",
    options={"billing": "Payments", "bugs": "Broken things", "other": "Anything else"},
)


def answer(value: Any, *, kind: str = "noul", usage: dict[str, int] | None = None) -> bytes:
    body = {"type": kind, "noul": value} if kind == "noul" else {"type": kind, **value}
    return json.dumps(
        {
            "model": DEFAULT_JEV_MODEL,
            "answers": {"question": body},
            "usage": usage if usage is not None else {"input_tokens": 120, "output_tokens": 0},
        }
    ).encode()


def ok(body: bytes) -> HttpResponse:
    return HttpResponse(200, {"Content-Type": "application/json"}, body)


class FakeJev:
    """A scripted stand-in for TypeSafe's API. Records each request it receives."""

    def __init__(self, *responses: HttpResponse | BaseException, delay: float = 0) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
            if isinstance(response, BaseException):
                raise response
            return response
        finally:
            self.in_flight -= 1


def client(fake: FakeJev, tmp_path: Path, *, key: str | None = FAKE_KEY) -> JevClient:
    return JevClient(key=lambda: key, http=fake, data_dir=tmp_path, limiter=JevLimiter())


def usage_rows(tmp_path: Path) -> list[dict[str, Any]]:
    with usage_db(tmp_path) as db:
        rows = db.execute(
            "SELECT provider, model, status, tokens, run_id FROM usage_invocations"
        ).fetchall()
    return [dict(row) for row in rows]


# Requests ---------------------------------------------------------------------------


async def test_yes_no_request_shape_bearer_key_and_fixed_endpoint(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.9)))

    result = await client(fake, tmp_path).classify(
        model=DEFAULT_JEV_MODEL, state="Please refund me", question=YES_NO, run_id="run-1"
    )

    request = fake.requests[0]
    assert (request.method, request.url) == ("POST", JEV_ENDPOINT)
    assert JEV_ENDPOINT == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["Content-Type"] == "application/json"
    assert LONG_CONTEXT_HEADER not in request.headers
    assert request.timeout_seconds == 15
    assert json.loads(request.body or b"") == {
        "model": "jev-1.14.0",
        "state": "Please refund me",
        "questions": {
            "question": {
                "type": "noul",
                "instructions": "Does the writer ask for a refund?",
                "criteria": {"true": "Asks for money back.", "false": "Anything else."},
            }
        },
    }
    assert (result.answer.label, result.answer.score) == ("yes", 0.9)
    assert result.answer.probabilities == {"yes": 0.9, "no": pytest.approx(0.1)}
    assert (result.input_tokens, result.output_tokens) == (120, 0)


async def test_choice_options_are_sent_as_criteria_and_long_context_header(
    tmp_path: Path,
) -> None:
    fake = FakeJev(
        ok(
            answer(
                {
                    "choice": "bugs",
                    "confidence": 0.8,
                    "probabilities": {"billing": 0.1, "bugs": 0.8, "other": 0.1},
                },
                kind="choice",
            )
        )
    )

    result = await client(fake, tmp_path).classify(
        model="jev-1.13.0",
        state={"subject": "Crash", "body": "It crashes"},
        question=CHOICE,
        long_context=True,
    )

    body = json.loads(fake.requests[0].body or b"")
    assert body["questions"]["question"] == {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {"billing": "Payments", "bugs": "Broken things", "other": "Anything else"},
    }
    assert body["state"] == {"subject": "Crash", "body": "It crashes"}
    assert fake.requests[0].headers[LONG_CONTEXT_HEADER] == "true"
    assert (result.answer.label, result.answer.score) == ("bugs", 0.8)
    assert result.answer.probabilities["bugs"] == 0.8
    assert result.requested_model == "jev-1.13.0"


def test_yes_no_without_criteria_sends_no_criteria() -> None:
    body = build_request(model="m", state="s", question=JevQuestion("yes-no", "Is it spam?"))
    assert body["questions"]["question"] == {"type": "noul", "instructions": "Is it spam?"}


@pytest.mark.parametrize(("score", "label"), [(0.6, "yes"), (0.5999, "no"), (1.0, "yes")])
async def test_threshold_is_inclusive(tmp_path: Path, score: float, label: str) -> None:
    result = await client(FakeJev(ok(answer(score))), tmp_path).classify(
        model=DEFAULT_JEV_MODEL, state="x", question=YES_NO, threshold=0.6
    )
    assert result.answer.label == label


# Answers that are never trusted ------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        answer(math.nan),
        answer(math.inf),
        answer(-0.1),
        answer(1.1),
        answer("0.9"),
        answer(True),
        json.dumps({"model": "jev-1.14.0", "answers": {}}).encode(),
        json.dumps({"answers": {"question": {"type": "noul", "noul": 0.5}}}).encode(),
        answer({"choice": "billing", "confidence": 0.5, "probabilities": {}}, kind="choice"),
        b"not json",
        b"[]",
    ],
)
async def test_malformed_yes_no_answers_are_errors_never_a_no(tmp_path: Path, body: bytes) -> None:
    with pytest.raises(JevError) as caught:
        await client(FakeJev(ok(body)), tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=YES_NO
        )
    assert caught.value.kind == "invalid_response"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "value",
    [
        {"choice": "sales", "confidence": 0.9, "probabilities": {"sales": 0.9}},
        {"choice": "bugs", "confidence": 1.5, "probabilities": {"bugs": 0.9}},
        {"choice": "bugs", "confidence": 0.9},
        {"choice": "bugs", "confidence": 0.9, "probabilities": {"sales": 0.9}},
    ],
)
async def test_malformed_choice_answers_are_errors(tmp_path: Path, value: dict[str, Any]) -> None:
    with pytest.raises(JevError) as caught:
        await client(FakeJev(ok(answer(value, kind="choice"))), tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=CHOICE
        )
    assert caught.value.kind == "invalid_response"


async def test_empty_or_oversized_state_is_refused_before_any_call(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.5)))
    jev = client(fake, tmp_path)
    states: list[Any] = ["", "   ", {}, [], None, "x" * (MAX_STATE_BYTES + 1)]
    for state in states:
        with pytest.raises(ValueError):
            await jev.classify(model=DEFAULT_JEV_MODEL, state=state, question=YES_NO)
    assert fake.requests == []
    assert usage_rows(tmp_path) == []


# Errors and retries -----------------------------------------------------------------


async def test_rejected_key_never_echoes_the_body_or_key(tmp_path: Path) -> None:
    fake = FakeJev(HttpResponse(401, {}, b'{"detail": "bad key sk-secret-echo"}'))
    with pytest.raises(JevError) as caught:
        await client(fake, tmp_path).classify(model=DEFAULT_JEV_MODEL, state="x", question=YES_NO)
    error = caught.value
    assert (error.kind, error.retryable, error.status) == ("auth", False, 401)
    assert "sk-secret-echo" not in error.message
    assert FAKE_KEY not in error.message


async def test_validation_errors_keep_field_locations_only(tmp_path: Path) -> None:
    body = json.dumps(
        {"detail": [{"loc": ["body", "state"], "msg": "bad", "input": "PRIVATE STATE TEXT"}]}
    ).encode()
    with pytest.raises(JevError) as caught:
        await client(FakeJev(HttpResponse(422, {}, body)), tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="PRIVATE STATE TEXT", question=YES_NO
        )
    assert caught.value.kind == "rejected"
    assert "body.state" in caught.value.message
    assert "PRIVATE" not in caught.value.message


@pytest.mark.parametrize(
    ("response", "kind", "retryable"),
    [
        (HttpResponse(404, {}, b""), "model_unavailable", False),
        (HttpResponse(400, {}, b'{"detail": "too long"}'), "rejected", False),
        (HttpResponse(500, {}, b""), "unavailable", True),
        (HttpResponse(503, {}, b""), "unavailable", True),
        (HttpResponse(302, {"Location": "https://elsewhere.example"}, b""), "unavailable", False),
    ],
)
async def test_status_mapping(
    tmp_path: Path, response: HttpResponse, kind: str, retryable: bool
) -> None:
    with pytest.raises(JevError) as caught:
        await client(FakeJev(response), tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=YES_NO
        )
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)


@pytest.mark.parametrize("status", [429, 529])
async def test_rate_limit_starts_a_shared_cooldown(tmp_path: Path, status: int) -> None:
    limiter = JevLimiter()
    fake = FakeJev(HttpResponse(status, {"Retry-After": "120"}, b""))
    first = JevClient(key=lambda: FAKE_KEY, http=fake, data_dir=tmp_path, limiter=limiter)
    second = JevClient(key=lambda: FAKE_KEY, http=fake, data_dir=tmp_path, limiter=limiter)

    with pytest.raises(JevError) as caught:
        await first.classify(model=DEFAULT_JEV_MODEL, state="x", question=YES_NO)
    assert caught.value.kind == "rate_limited"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 120
    with pytest.raises(JevError) as again:
        await second.classify(model=DEFAULT_JEV_MODEL, state="y", question=YES_NO)
    assert again.value.kind == "rate_limited"
    assert len(fake.requests) == 1
    assert 0 < limiter.cooldown_remaining() <= 120
    # Both calls are recorded, though the second never reached Jev.
    assert [row["status"] for row in usage_rows(tmp_path)] == ["failed", "failed"]


def test_cooldown_is_kept_between_one_and_five_minutes() -> None:
    limiter = JevLimiter(clock=lambda: 0.0)
    assert limiter.start_cooldown(2) == 60
    assert limiter.start_cooldown(10_000) == 300


def test_retry_after_accepts_seconds_and_http_dates() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
    assert retry_after_seconds({"retry-after": "30"}, now=now) == 30
    assert retry_after_seconds({"Retry-After": "Fri, 13 Nov 2026 12:02:00 GMT"}, now=now) == 120
    assert retry_after_seconds({"Retry-After": "soon"}, now=now) is None
    assert retry_after_seconds({}, now=now) is None


def test_a_retry_after_with_a_non_ascii_digit_is_ignored_instead_of_raising() -> None:
    """Nightly audit, 27 November: headers are read as Latin-1, and "³" passed
    str.isdigit() then made float() raise, so an HTTP request step reported an internal
    configuration error instead of the server's status."""
    for value in ("\xb3", "1\xb2", "\u0663"):
        assert retry_after_seconds({"Retry-After": value}) is None, value


async def test_network_errors_name_the_type_only(tmp_path: Path) -> None:
    fake = FakeJev(OSError("connect to 10.0.0.5 with token abc failed"))
    with pytest.raises(JevError) as caught:
        await client(fake, tmp_path).classify(model=DEFAULT_JEV_MODEL, state="x", question=YES_NO)
    assert caught.value.kind == "network"
    assert caught.value.retryable is True
    assert "token" not in caught.value.message
    assert "OSError" in caught.value.message


async def test_slow_answers_time_out(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.5)), delay=1)
    with pytest.raises(JevError) as caught:
        await client(fake, tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=YES_NO, timeout=0.05
        )
    assert caught.value.kind == "timeout"
    assert caught.value.retryable is True


async def test_stop_closes_an_in_flight_call_and_records_it_as_cancelled(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.5)), delay=5)
    stop = threading.Event()

    async def press_stop() -> None:
        while not fake.requests:
            await asyncio.sleep(0.01)
        stop.set()

    with pytest.raises(JevError) as caught:
        await asyncio.gather(
            client(fake, tmp_path).classify(
                model=DEFAULT_JEV_MODEL, state="x", question=YES_NO, cancel_event=stop
            ),
            press_stop(),
        )
    assert caught.value.kind == "cancelled"
    assert [row["status"] for row in usage_rows(tmp_path)] == ["cancelled"]


async def test_stop_before_sending_makes_no_request(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.5)))
    stop = threading.Event()
    stop.set()
    with pytest.raises(JevError) as caught:
        await client(fake, tmp_path).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=YES_NO, cancel_event=stop
        )
    assert caught.value.kind == "cancelled"
    assert fake.requests == []


async def test_no_key_is_a_configuration_error(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.5)))
    with pytest.raises(JevError) as caught:
        await client(fake, tmp_path, key=None).classify(
            model=DEFAULT_JEV_MODEL, state="x", question=YES_NO
        )
    assert caught.value.kind == "not_configured"
    assert fake.requests == []


async def test_at_most_four_calls_are_in_flight(tmp_path: Path) -> None:
    fake = FakeJev(ok(answer(0.7)), delay=0.05)
    jev = client(fake, tmp_path)

    results = await asyncio.gather(
        *(
            jev.classify(model=DEFAULT_JEV_MODEL, state=f"item {n}", question=YES_NO)
            for n in range(10)
        )
    )

    assert len(results) == 10
    assert fake.max_in_flight == 4


# Usage ------------------------------------------------------------------------------


async def test_every_call_writes_one_usage_row_including_failures(tmp_path: Path) -> None:
    jev = JevClient(
        key=lambda: FAKE_KEY,
        http=FakeJev(ok(answer(0.9)), HttpResponse(500, {}, b"")),
        data_dir=tmp_path,
        limiter=JevLimiter(),
    )
    await jev.classify(model=DEFAULT_JEV_MODEL, state="a", question=YES_NO, run_id="run-7")
    with pytest.raises(JevError):
        await jev.classify(model=DEFAULT_JEV_MODEL, state="b", question=YES_NO, run_id="run-7")

    rows = usage_rows(tmp_path)
    assert [(r["provider"], r["model"], r["status"], r["run_id"]) for r in rows] == [
        ("jev", "jev-1.14.0", "completed", "run-7"),
        ("jev", "jev-1.14.0", "failed", "run-7"),
    ]
    assert json.loads(rows[0]["tokens"]) == {
        "input_tokens": 120,
        "output_tokens": 0,
        "total_tokens": 120,
    }
    providers = usage_history(tmp_path, resolve_range())["activity"]["providers"]
    assert [(p["provider"], p["calls"]) for p in providers] == [("jev", 2)]
    assert FAKE_KEY not in json.dumps(rows)


def test_jev_has_a_usage_label_but_is_not_a_coding_provider() -> None:
    from gofer.core.provider_capabilities import CLI_PROVIDERS
    from gofer.core.provider_usage import PROVIDER_LABELS
    from gofer.rattish.provider_runtime import default_provider_subscriptions

    assert PROVIDER_LABELS["jev"] == "Jev (TypeSafe)"
    assert "jev" not in CLI_PROVIDERS
    assert "jev" not in default_provider_subscriptions()
    providers = Path(__file__).parents[2] / "rattish" / "providers"
    assert not list(providers.glob("jev*.json"))


async def test_model_list_reads_names(tmp_path: Path) -> None:
    fake = FakeJev(
        ok(
            json.dumps(
                {
                    "models": [
                        {"name": "jev-latest", "description": "", "release_date": "2026-10-14"},
                        {"name": "jev-preview", "description": "", "release_date": "2026-10-14"},
                    ]
                }
            ).encode()
        )
    )
    assert await client(fake, tmp_path).list_models() == ["jev-latest", "jev-preview"]
    assert (fake.requests[0].method, fake.requests[0].url) == ("GET", JEV_MODELS_ENDPOINT)
    assert [row["status"] for row in usage_rows(tmp_path)] == ["completed"]


# Keychain settings ------------------------------------------------------------------


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail = False

    def get(self, account: str) -> str | None:
        if self.fail:
            raise RuntimeError("locked")
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.values[account] = value

    def delete(self, account: str) -> None:
        if self.fail:
            raise RuntimeError("locked")
        self.values.pop(account, None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(jev_settings, "_secret_store", lambda: memory)
    jev_settings._cache.clear()
    return memory


def test_key_lives_in_the_keychain_only(tmp_path: Path, store: MemoryStore) -> None:
    status = jev_settings.save_jev_key(f"  {FAKE_KEY}\n", tmp_path)

    assert status["configured"] is True
    assert FAKE_KEY not in json.dumps(status)
    assert len(store.values) == 1
    ((account, raw),) = store.values.items()
    assert account.endswith(":jev")
    assert json.loads(raw)["key"] == FAKE_KEY
    settings_file = (tmp_path / jev_settings.SETTINGS_FILE).read_text()
    assert FAKE_KEY not in settings_file
    assert (tmp_path / jev_settings.SETTINGS_FILE).stat().st_mode & 0o077 == 0
    assert jev_settings.read_jev_key(tmp_path) == FAKE_KEY
    assert jev_settings.jev_key_configured(tmp_path)

    jev_settings.remove_jev_key(tmp_path)
    assert store.values == {}
    assert jev_settings.read_jev_key(tmp_path) is None
    assert not jev_settings.jev_key_configured(tmp_path)


def test_each_data_folder_has_its_own_key(tmp_path: Path, store: MemoryStore) -> None:
    jev_settings.save_jev_key("key-one", tmp_path / "a")
    jev_settings.save_jev_key("key-two", tmp_path / "b")
    assert jev_settings.read_jev_key(tmp_path / "a") == "key-one"
    assert jev_settings.read_jev_key(tmp_path / "b") == "key-two"


def test_turning_jev_off_blocks_calls_and_keeps_the_key(tmp_path: Path, store: MemoryStore) -> None:
    jev_settings.save_jev_key(FAKE_KEY, tmp_path)
    jev_settings.save_jev_settings({"enabled": False}, tmp_path)
    with pytest.raises(JevError) as caught:
        jev_settings.read_jev_key(tmp_path)
    assert caught.value.kind == "not_configured"
    assert store.values
    jev_settings.save_jev_settings({"enabled": True}, tmp_path)
    assert jev_settings.read_jev_key(tmp_path) == FAKE_KEY


@pytest.mark.parametrize("bad", ["", "   ", "two words", "a\tb", None, 5, "x" * 5000])
def test_bad_keys_are_refused(tmp_path: Path, store: MemoryStore, bad: Any) -> None:
    with pytest.raises(jev_settings.JevSettingsError):
        jev_settings.save_jev_key(bad, tmp_path)
    assert store.values == {}


def test_locked_keychain_gives_a_safe_error(tmp_path: Path, store: MemoryStore) -> None:
    store.fail = True
    with pytest.raises(jev_settings.JevSettingsError) as caught:
        jev_settings.save_jev_key(FAKE_KEY, tmp_path)
    assert FAKE_KEY not in str(caught.value)
    assert not jev_settings.jev_key_configured(tmp_path)


def test_a_key_in_the_environment_is_ignored_and_never_inherited(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.utils.process import build_subprocess_env

    monkeypatch.setenv("JEV_API_KEY", "exported-key")
    assert jev_settings.read_jev_key(tmp_path) is None
    jev_settings.save_jev_key(FAKE_KEY, tmp_path)
    env = build_subprocess_env()
    assert "JEV_API_KEY" not in env
    assert FAKE_KEY not in json.dumps(env)


async def test_key_check_records_models_or_the_error(tmp_path: Path, store: MemoryStore) -> None:
    jev_settings.save_jev_key(FAKE_KEY, tmp_path)
    models = ok(json.dumps({"models": [{"name": "jev-latest"}]}).encode())
    status = await jev_settings.check_jev_key(tmp_path, client(FakeJev(models), tmp_path))
    assert status["models"] == ["jev-latest"]
    assert status["checkError"] is None
    assert status["checkedAt"]

    status = await jev_settings.check_jev_key(
        tmp_path, client(FakeJev(HttpResponse(401, {}, b"")), tmp_path)
    )
    assert "rejected the key" in status["checkError"]
    assert status["models"] == ["jev-latest"]


async def test_default_client_reads_the_saved_key(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    jev_settings.save_jev_key(FAKE_KEY, tmp_path)
    fake = FakeJev(ok(answer(0.4)))
    jev = jev_settings.default_jev_client(tmp_path)
    jev.http = fake
    jev.limiter = JevLimiter()
    result = await jev.classify(model=DEFAULT_JEV_MODEL, state="x", question=YES_NO)
    assert result.answer.label == "no"
    assert fake.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"


def test_secret_store_delete_treats_a_missing_entry_as_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from keyring.backends import SecretService
    from keyring.errors import PasswordDeleteError

    from gofer.devices.storage import OSSecretStore, StorageError

    class Backend:
        priority = 5
        values: dict[tuple[str, str], str] = {("Raticode provider keys", "a"): "v"}

        def get_password(self, service: str, account: str) -> str | None:
            return self.values.get((service, account))

        def delete_password(self, service: str, account: str) -> None:
            if (service, account) not in self.values:
                raise PasswordDeleteError("missing")
            del self.values[(service, account)]

    monkeypatch.setattr(SecretService, "Keyring", Backend)
    store = OSSecretStore(service="Raticode provider keys")
    store.delete("a")
    store.delete("a")
    assert Backend.values == {}

    def broken(self: Any, service: str, account: str) -> None:
        raise RuntimeError("bus down")

    monkeypatch.setattr(Backend, "delete_password", broken)
    with pytest.raises(StorageError):
        store.delete("a")


# Routes and CLI ---------------------------------------------------------------------


def test_routes_save_check_toggle_and_remove_without_returning_the_key(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_ui_server import _request

    assert _request(tmp_path, "GET", "/api/classifiers/jev", authenticated=False).status == 401
    status = cast(dict[str, Any], _request(tmp_path, "GET", "/api/classifiers/jev").json())
    assert status["configured"] is False
    assert status["defaultModel"] == "jev-1.14.0"

    saved = _request(tmp_path, "POST", "/api/classifiers/jev/key", body={"key": FAKE_KEY})
    assert saved.status == 200
    assert FAKE_KEY not in saved.text()
    assert cast(dict[str, Any], saved.json())["configured"] is True
    read = _request(tmp_path, "GET", "/api/classifiers/jev")
    assert FAKE_KEY not in read.text()

    bad = _request(tmp_path, "POST", "/api/classifiers/jev/key", body={"key": "has space"})
    assert bad.status == 400
    assert "has space" not in bad.text()

    async def fake_check(data_dir: Path | None = None, client: Any = None) -> dict[str, Any]:
        return {**jev_settings.jev_status(data_dir), "models": ["jev-latest"]}

    monkeypatch.setattr(jev_settings, "check_jev_key", fake_check)
    checked = _request(tmp_path, "POST", "/api/classifiers/jev/check", body={})
    assert cast(dict[str, Any], checked.json())["models"] == ["jev-latest"]

    off = _request(tmp_path, "POST", "/api/classifiers/jev/settings", body={"enabled": False})
    assert cast(dict[str, Any], off.json())["enabled"] is False
    unknown = _request(tmp_path, "POST", "/api/classifiers/jev/settings", body={"url": "x"})
    assert unknown.status == 400

    removed = _request(tmp_path, "DELETE", "/api/classifiers/jev/key")
    assert cast(dict[str, Any], removed.json())["configured"] is False
    assert store.values == {}


def test_agent_tokens_cannot_change_jev_settings() -> None:
    from gofer.ui.headless import agent_request_refusal

    assert agent_request_refusal("GET", "/api/classifiers/jev") is None
    for method, path in (
        ("POST", "/api/classifiers/jev/key"),
        ("DELETE", "/api/classifiers/jev/key"),
        ("POST", "/api/classifiers/jev/check"),
        ("POST", "/api/classifiers/jev/settings"),
    ):
        assert agent_request_refusal(method, path)


def test_set_key_never_takes_the_key_as_an_argument() -> None:
    from typer.main import get_command

    from gofer.cli.main import app

    group = get_command(app).commands["provider"].commands["jev"]  # type: ignore[attr-defined]
    set_key = group.commands["set-key"]
    assert [param.name for param in set_key.params if not param.opts[0].startswith("-")] == []
    assert {"status", "check", "set-key", "remove-key", "enable", "disable"} == set(group.commands)


def test_set_key_reads_stdin_and_refuses_inside_agent_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.commands import jev as jev_cli
    from gofer.cli.main import app

    sent: list[tuple[str, str, Any]] = []

    def fake_finish(current: Any, call: Any, text: Any = None, **_: Any) -> None:
        class Backend:
            def request(self, method: str, path: str, body: Any = None, **__: Any) -> Any:
                sent.append((method, path, body))
                return {}

        call(Backend(), None)

    monkeypatch.setattr(jev_cli, "finish", fake_finish)
    monkeypatch.delenv("RATICODE_AGENT_SESSION", raising=False)
    result = CliRunner().invoke(app, ["provider", "jev", "set-key", "--stdin"], input=FAKE_KEY)
    assert result.exit_code == 0, result.output
    assert sent == [("POST", "/classifiers/jev/key", {"key": FAKE_KEY})]
    assert FAKE_KEY not in result.output

    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    sent.clear()
    refused = CliRunner().invoke(app, ["provider", "jev", "set-key", "--stdin"], input=FAKE_KEY)
    assert refused.exit_code != 0
    assert sent == []
