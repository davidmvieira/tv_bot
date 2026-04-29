from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import httpx


def _normalize_entry(extinf: str, url: str) -> Tuple[str, str]:
    return extinf.strip(), url.strip()


def _parse_m3u_entries(text: str) -> List[Tuple[str, str]]:
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    entries: List[Tuple[str, str]] = []
    extinf: Optional[str] = None
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#EXTINF"):
            extinf = s
            continue
        if s.startswith("#"):
            continue
        if extinf is not None:
            entries.append(_normalize_entry(extinf, s))
            extinf = None
    return entries


def _render_m3u(entries: Iterable[Tuple[str, str]]) -> str:
    out = ["#EXTM3U"]
    for extinf, url in entries:
        out.append(extinf)
        out.append(url)
    return "\n".join(out) + "\n"


async def _fetch_text(url: str, timeout_seconds: float) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        r = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive",})
        r.raise_for_status()
        return r.text


def aggregate_m3u_files(paths: List[Path], output_path: Path) -> int:
    seen = set()
    merged: List[Tuple[str, str]] = []
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for extinf, url in _parse_m3u_entries(text):
            key = (extinf, url)
            if key in seen:
                continue
            seen.add(key)
            merged.append((extinf, url))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_m3u(merged), encoding="utf-8")
    return len(merged)


async def aggregate_m3u_from_urls(urls: List[str], output_path: Path, *, timeout_seconds: float = 10) -> int:
    seen = set()
    merged: List[Tuple[str, str]] = []
    for u in urls:
        try:
            text = await _fetch_text(u, timeout_seconds)
        except Exception:
            continue
        for extinf, url in _parse_m3u_entries(text):
            key = (extinf, url)
            if key in seen:
                continue
            seen.add(key)
            merged.append((extinf, url))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_m3u(merged), encoding="utf-8")
    return len(merged)

