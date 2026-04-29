from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@dataclass(frozen=True)
class M3UValidationResult:
    ok: bool
    http_status: Optional[int]
    channels_count: int
    error: Optional[str]
    content_type: Optional[str] = None
    content: Optional[str] = None


def _count_channels(m3u_text: str) -> int:
    # canais normalmente tem EXTINF por entrada
    return m3u_text.count("#EXTINF")


def _is_m3u(text: str) -> bool:
    return "#EXTM3U" in text[:4096]


def _stream_urls_from_m3u(text: str, limit: int) -> List[str]:
    if limit <= 0:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: List[str] = []
    for ln in lines:
        if ln.startswith("#"):
            continue
        if ln.startswith("http://") or ln.startswith("https://"):
            out.append(ln)
            if len(out) >= limit:
                break
    return out


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=6),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
)
async def _fetch_text(url: str, timeout_seconds: float) -> Tuple[int, str, Optional[str]]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        r = await client.get(url, headers={"User-Agent": "iptv-bot/0.1"})
        status = r.status_code
        ctype = r.headers.get("content-type")
        text = r.text
        return status, text, ctype


@retry(
    reraise=True,
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=3),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
)
async def _probe_stream(url: str, timeout_seconds: float) -> int:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        # HEAD nem sempre funciona, GET curto costuma ser melhor
        r = await client.get(url, headers={"Range": "bytes=0-2048", "User-Agent": "iptv-bot/0.1"})
        return r.status_code


async def validate_m3u_url(
    url: str,
    *,
    timeout_seconds: float = 10,
    stream_sample_validate: int = 0,
) -> M3UValidationResult:
    try:
        status, text, ctype = await _fetch_text(url, timeout_seconds)
    except Exception as e:  # noqa: BLE001
        return M3UValidationResult(ok=False, http_status=None, channels_count=0, error=str(e))

    if status != 200:
        return M3UValidationResult(ok=False, http_status=status, channels_count=0, error=f"http_status={status}", content_type=ctype, content=None)

    if not _is_m3u(text):
        return M3UValidationResult(ok=False, http_status=status, channels_count=0, error="missing_extm3u", content_type=ctype, content=text)

    channels = _count_channels(text)

    # opcional: tentar validar N streams dentro da lista
    if stream_sample_validate > 0:
        stream_urls = _stream_urls_from_m3u(text, stream_sample_validate)
        good = 0
        for su in stream_urls:
            try:
                s = await _probe_stream(su, timeout_seconds)
                if 200 <= s < 500:
                    good += 1
            except Exception:
                pass
        if stream_urls and good == 0:
            return M3UValidationResult(
                ok=False,
                http_status=status,
                channels_count=channels,
                error="stream_sample_failed",
                content_type=ctype,
            )

    return M3UValidationResult(ok=True, http_status=status, channels_count=channels, error=None, content_type=ctype, content=text)

