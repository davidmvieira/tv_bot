from __future__ import annotations

import re
from typing import Iterable, List
from urllib.parse import urlparse


_URL_RE = re.compile(
    r"(?P<url>https?://[^\s<>\]\)\"']+)",
    flags=re.IGNORECASE,
)


def _clean_url(u: str) -> str:
    u = u.strip().strip(").,;:!?\"'[]<>")
    u = u.replace("\\", "")
    return u


def _looks_like_iptv_url(u: str) -> bool:
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return False
    if not p.netloc:
        return False

    path = (p.path or "").lower()
    if path.endswith(".m3u") or path.endswith(".m3u8"):
        return True

    # endpoints comuns de playlist
    q = (p.query or "").lower()
    # casos frequentes: type=m3u_plus
    if "type=m3u_plus" in q or "type=m3u" in q:
        return True
    if "m3u" in q:
        return True
    if "get.php" in path and ("username=" in q or "password=" in q):
        return True
    if "playlist" in path or "m3u" in path:
        return True

    return False


def extract_candidate_urls(text: str) -> List[str]:
    if not text:
        return []
    urls: List[str] = []
    seen = set()

    for m in _URL_RE.finditer(text):
        u = _clean_url(m.group("url"))
        if not u or u in seen:
            continue
        if _looks_like_iptv_url(u):
            urls.append(u)
            seen.add(u)
    return urls


def extract_candidate_urls_from_messages(messages: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for t in messages:
        for u in extract_candidate_urls(t):
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out

