"""Shared async HTTP plumbing for all upstream providers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("providers")

_client: httpx.AsyncClient | None = None

TIMEOUT = httpx.Timeout(15.0, connect=8.0)
MAX_RETRIES = 3


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": "ai-trading-training-bot/1.0"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.status = status
        self.permanent = permanent


async def request(method: str, url: str, **kw: Any) -> Any:
    """Issue a request with exponential backoff, returning parsed JSON.

    Retries on transport errors, 429, and 5xx. A 4xx other than 429 is a
    permanent error (bad symbol, bad params) and fails immediately -- retrying
    would just burn rate-limit budget for a call that cannot succeed.
    """
    delay = 1.0
    last: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = await client().request(method, url, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise ProviderError(f"{resp.status_code} from {url}",
                                    status=resp.status_code)
            if resp.status_code >= 400:
                raise ProviderError(
                    f"{resp.status_code} from {url}: {resp.text[:200]}",
                    status=resp.status_code, permanent=True,
                )
            return resp.json()
        except ProviderError as e:
            if e.permanent:
                raise
            last = e
        except (httpx.HTTPError, ValueError) as e:
            last = e

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(delay)
            delay *= 2

    raise ProviderError(f"failed after {MAX_RETRIES} attempts: {last}")


async def get(url: str, **kw: Any) -> Any:
    return await request("GET", url, **kw)


async def post(url: str, **kw: Any) -> Any:
    return await request("POST", url, **kw)
