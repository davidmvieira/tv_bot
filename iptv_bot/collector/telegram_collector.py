from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.tl import functions


@dataclass(frozen=True)
class TargetCheckResult:
    target: str
    ok: bool
    resolved_id: Optional[int]
    title: Optional[str]
    kind: Optional[str]
    error: Optional[str]

def _normalize_target(raw: str):
    """
    Accepts:
    - numeric ids like -100123...
    - @usernames
    - t.me links
    - Telegram Web links containing '#-100..._msgId'
    """
    s = (raw or "").strip()
    if not s:
        return raw

    # Telegram Web: https://web.telegram.org/a/#-1002764542068_2961
    if "web.telegram.org" in s and "#" in s:
        frag = s.split("#", 1)[1]
        # frag may be like -100..._...
        if "_" in frag:
            frag = frag.split("_", 1)[0]
        s = frag

    # If it still looks like a URL, try to extract username from t.me/xxx
    if s.startswith("http://") or s.startswith("https://"):
        p = urlparse(s)
        host = (p.netloc or "").lower()
        path = (p.path or "").strip("/")
        if host in ("t.me", "telegram.me") and path:
            s = path  # may be username or joinchat code

    # Numeric IDs must be int for Telethon; otherwise it may try username lookup
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except Exception:
            return s

    return s


async def check_targets(
    *,
    api_id: int,
    api_hash: str,
    session_name: str,
    targets: Sequence[str],
) -> List[TargetCheckResult]:
    if not targets:
        return []

    results: List[TargetCheckResult] = []
    async with TelegramClient(session_name, api_id, api_hash) as client:
        for t in targets:
            try:
                entity = await client.get_entity(_normalize_target(str(t)))
                resolved_id = getattr(entity, "id", None)
                title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(t)
                kind = entity.__class__.__name__
                results.append(
                    TargetCheckResult(
                        target=str(t),
                        ok=True,
                        resolved_id=int(resolved_id) if resolved_id is not None else None,
                        title=str(title) if title is not None else None,
                        kind=str(kind),
                        error=None,
                    )
                )
            except Exception as e:  # noqa: BLE001
                results.append(
                    TargetCheckResult(
                        target=str(t),
                        ok=False,
                        resolved_id=None,
                        title=None,
                        kind=None,
                        error=str(e),
                    )
                )
    return results


async def collect_recent_messages(
    *,
    api_id: int,
    api_hash: str,
    session_name: str,
    targets: Sequence[str],
    limit_per_target: int = 200,
) -> List[str]:
    if not targets:
        return []

    messages: List[str] = []
    async with TelegramClient(session_name, api_id, api_hash) as client:
        for t in targets:
            try:
                entity = await client.get_entity(_normalize_target(str(t)))
            except Exception:
                # alvo inválido ou sem acesso
                continue

            async def _consume_messages(reply_to: Optional[int] = None) -> None:
                async for msg in client.iter_messages(entity, limit=limit_per_target, reply_to=reply_to):
                    if not msg:
                        continue
                    # Em alguns chats o conteúdo pode vir em propriedades diferentes.
                    candidates = [
                        getattr(msg, "raw_text", None),
                        getattr(msg, "text", None),
                        getattr(msg, "message", None),
                    ]
                    for txt in candidates:
                        if isinstance(txt, str) and txt.strip():
                            messages.append(txt)
                            break

            # Mensagens do "main" (sem tópico)
            await _consume_messages(reply_to=None)

            # Se for um supergrupo com tópicos (forum), também percorre tópicos.
            if bool(getattr(entity, "forum", False)):
                try:
                    topics = await client(
                        functions.channels.GetForumTopicsRequest(
                            channel=entity,
                            offset_date=None,
                            offset_id=0,
                            offset_topic=0,
                            limit=50,
                            q="",
                        )
                    )
                    for tp in getattr(topics, "topics", []) or []:
                        top_id = getattr(tp, "id", None)
                        if isinstance(top_id, int):
                            await _consume_messages(reply_to=top_id)
                except Exception:
                    # não suportado / sem permissão / não é forum de fato
                    pass

    return messages

