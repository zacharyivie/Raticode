"""A stand-in for sherpa-onnx's streaming recognizer, driven by a script of events.

Each chunk of real (non-silent) audio advances the script by one step. A step is the
text the recognizer shows after that chunk and whether it ends a phrase there (an
endpoint). ``final`` is what the last phrase reads once the dictation is finished.
The fake records every call, so tests can check the lead-in and tail silence and the
order of audio without a real model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeStream:
    samples: list[list[float]] = field(default_factory=list)
    pending: int = 0
    text: str = ""
    endpoint: bool = False
    finished: bool = False


@dataclass
class FakeSherpaEngine:
    script: list[tuple[str, bool]] = field(default_factory=list)
    final: str = ""
    calls: list[str] = field(default_factory=list)
    streams: list[FakeStream] = field(default_factory=list)
    step: int = 0

    def create_stream(self) -> FakeStream:
        self.calls.append("create_stream")
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def accept_waveform(self, stream: FakeStream, sample_rate: int, samples: Any) -> None:
        assert sample_rate == 16_000
        values = [float(value) for value in samples]
        stream.samples.append(values)
        if any(values):
            stream.pending += 1
            self.calls.append(f"audio:{len(values)}")
        else:
            self.calls.append(f"silence:{len(values)}")

    def is_ready(self, stream: FakeStream) -> bool:
        return stream.pending > 0 or (stream.finished and stream.text != self.final)

    def decode_stream(self, stream: FakeStream) -> None:
        if stream.pending:
            stream.pending -= 1
            if self.step < len(self.script):
                stream.text, stream.endpoint = self.script[self.step]
                self.step += 1
        elif stream.finished:
            stream.text = self.final

    def get_result(self, stream: FakeStream) -> str:
        return stream.text

    def is_endpoint(self, stream: FakeStream) -> bool:
        return stream.endpoint

    def reset(self, stream: FakeStream) -> None:
        self.calls.append("reset")
        stream.text, stream.endpoint = "", False


class _BoundStream:
    """sherpa-onnx streams take audio themselves: ``stream.accept_waveform(rate, samples)``."""

    def __init__(self, engine: FakeSherpaEngine, stream: FakeStream) -> None:
        self.engine = engine
        self.stream = stream

    def accept_waveform(self, sample_rate: int, samples: Any) -> None:
        self.engine.accept_waveform(self.stream, sample_rate, samples)

    def input_finished(self) -> None:
        self.engine.calls.append("input_finished")
        self.stream.finished = True


class FakeRecognizerEngine:
    """The object ``offline_speech.new_engine`` returns, with sherpa-onnx's method names."""

    def __init__(self, script: list[tuple[str, bool]] | None = None, final: str = "") -> None:
        self.fake = FakeSherpaEngine(list(script or []), final)

    @property
    def calls(self) -> list[str]:
        return self.fake.calls

    def create_stream(self) -> _BoundStream:
        return _BoundStream(self.fake, self.fake.create_stream())

    def is_ready(self, stream: _BoundStream) -> bool:
        return self.fake.is_ready(stream.stream)

    def decode_stream(self, stream: _BoundStream) -> None:
        self.fake.decode_stream(stream.stream)

    def get_result(self, stream: _BoundStream) -> str:
        return self.fake.get_result(stream.stream)

    def is_endpoint(self, stream: _BoundStream) -> bool:
        return self.fake.is_endpoint(stream.stream)

    def reset(self, stream: _BoundStream) -> None:
        self.fake.reset(stream.stream)
