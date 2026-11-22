"""Shared async HTTP client: global concurrency limit, rotating User-Agents,
random jitter and retries with back-off."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, concurrency: int = 15, timeout: float = 20.0,
                 jitter: tuple[float, float] = (0.05, 0.6), max_retries: int = 2,
                 client: httpx.AsyncClient | None = None):
        self.sem = asyncio.Semaphore(concurrency)
        self.jitter = jitter
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={"Accept-Language": "en-US,en;q=0.9,he;q=0.8"},
        )

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"User-Agent": random.choice(USER_AGENTS), **kwargs.pop("headers", {})}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            async with self.sem:
                if self.jitter[1] > 0:
                    await asyncio.sleep(random.uniform(*self.jitter))
                try:
                    resp = await self._client.request(method, url, headers=headers, **kwargs)
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc
                    resp = None
            if resp is not None and resp.status_code not in RETRY_STATUS:
                return resp
            if attempt < self.max_retries:
                delay = 1.5 * (2 ** attempt) + random.random()
                if resp is not None and resp.status_code == 429:
                    ra = resp.headers.get("Retry-After", "")
                    delay = float(ra) if ra.isdigit() else delay * 2
                await asyncio.sleep(min(delay, 20))
        if resp is not None:
            return resp
        raise last_exc or RuntimeError(f"request failed: {url}")

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)

    async def get_json(self, url: str, **kw: Any) -> Any:
        resp = await self.get(url, headers={"Accept": "application/json", **kw.pop("headers", {})}, **kw)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, url: str, payload: Any, **kw: Any) -> Any:
        resp = await self.post(
            url, json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json", **kw.pop("headers", {})},
            **kw,
        )
        resp.raise_for_status()
        return resp.json()
