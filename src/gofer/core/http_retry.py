"""How many times an HTTP request is tried, shared by workflow steps and notifications."""

from __future__ import annotations

from pydantic import BaseModel


class HttpRetryPolicy(BaseModel):
    attempts: int = 1
    backoff_seconds: float = 0.0
    retry_on_statuses: list[int] = []
