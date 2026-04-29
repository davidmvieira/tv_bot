from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import orjson

from ..config import Settings
from .views import index_html, preview_html


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, status: int, text: str, content_type: str) -> None:
    body = text.encode("utf-8", errors="replace")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_lists(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    raw = db_path.read_bytes()
    if not raw:
        return []
    data = orjson.loads(raw)
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
    return out


def _run_cli(cmd: str, cwd: Path, timeout_s: int) -> Tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "iptv_bot.cli", cmd],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _fetch_m3u_preview_sync(url: str, timeout_s: float, max_chars: int) -> Tuple[int, str]:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_s) as client:
            r = client.get(url, headers={"User-Agent": "iptv-bot-webui/0.1"})
            if r.status_code != 200:
                return r.status_code, f"HTTP {r.status_code}\n\n{r.text[:2000]}"
            txt = r.text or ""
            if len(txt) > max_chars:
                txt = txt[:max_chars] + "\n\n... (truncado) ..."
            return 200, txt
    except Exception as e:  # noqa: BLE001
        return 500, str(e)


def _cache_filename(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"cached_{digest}.m3u"


def _cache_path(settings: Settings, url: str) -> Path:
    return settings.data_dir / "cached" / _cache_filename(url)


def _cache_url(settings: Settings, url: str) -> Optional[str]:
    p = _cache_path(settings, url)
    if p.exists():
        return f"/data/cached/{p.name}"
    return None


def make_handler(settings: Settings, repo_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # menos ruído no console
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path or "/"
            if route == "/preview":
                qs = urllib.parse.parse_qs(parsed.query or "")
                url = (qs.get("url", [""])[0] or "")
                return _html_response(self, 200, preview_html(url))


            if route in ("/", "/index.html"):
                return _html_response(self, 200, index_html())

            if route == "/api/lists":
                qs = urllib.parse.parse_qs(parsed.query or "")
                status = (qs.get("status", ["all"])[0] or "all").lower()
                items = _read_lists(settings.db_json)
                if status != "all":
                    items = [it for it in items if str(it.get("status", "")).lower() == status]
                for item in items:
                    if isinstance(item, dict):
                        item["cached"] = bool(_cache_url(settings, str(item.get("url", ""))))
                        item["cached_url"] = _cache_url(settings, str(item.get("url", "")))
                return _json_response(self, 200, {"ok": True, "items": items})

            if route == "/api/m3u_preview":
                qs = urllib.parse.parse_qs(parsed.query or "")
                url = (qs.get("url", [""])[0] or "").strip()
                if not (url.startswith("http://") or url.startswith("https://")):
                    return _text_response(self, 400, "URL inválida", "text/plain; charset=utf-8")
                force = (qs.get("refresh", ["0"])[0] or "0").lower() in ("1", "true", "yes")
                cache_path = _cache_path(settings, url)
                if cache_path.exists() and not force:
                    return _text_response(self, 200, cache_path.read_text(encoding="utf-8", errors="replace"), "text/plain; charset=utf-8")
                code, txt = _fetch_m3u_preview_sync(url, float(
                    settings.http_timeout_seconds), max_chars=250_000)
                return _text_response(self, code, txt, "text/plain; charset=utf-8")

            if route.startswith("/data/"):
                rel = route[len("/data/"):].lstrip("/")
                p = (settings.data_dir / rel).resolve()
                try:
                    p.relative_to(settings.data_dir.resolve())
                except Exception:
                    return _text_response(self, 403, "forbidden", "text/plain; charset=utf-8")
                if not p.exists() or not p.is_file():
                    return _text_response(self, 404, "not found", "text/plain; charset=utf-8")
                ctype = "application/octet-stream"
                if p.suffix.lower() == ".m3u" or p.suffix.lower() == ".m3u8":
                    ctype = "audio/x-mpegurl"
                if p.suffix.lower() == ".json":
                    ctype = "application/json; charset=utf-8"
                return _text_response(self, 200, p.read_text(encoding="utf-8", errors="replace"), ctype)

            return _text_response(self, 404, "not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path or "/"

            if route.startswith("/api/run/"):
                cmd = route.split("/api/run/", 1)[1].strip()
                if cmd not in {"collect", "revalidate", "run-once"}:
                    return _json_response(self, 400, {"ok": False, "error": "comando inválido"})

                # Evita múltiplas execuções simultâneas (simples)
                if not getattr(Handler, "_job_lock", None):
                    Handler._job_lock = threading.Lock()  # type: ignore[attr-defined]

                lock: threading.Lock = Handler._job_lock  # type: ignore[attr-defined]
                if not lock.acquire(blocking=False):
                    return _json_response(self, 409, {"ok": False, "error": "já existe um job em execução"})

                try:
                    timeout_s = int(os.getenv("WEB_JOB_TIMEOUT_SECONDS", "3600"))
                    code, out, err = _run_cli(cmd, cwd=repo_root, timeout_s=timeout_s)
                    return _json_response(
                        self,
                        200,
                        {"ok": code == 0, "exit_code": code, "stdout": out[-8000:], "stderr": err[-8000:]},
                    )
                except subprocess.TimeoutExpired:
                    return _json_response(self, 500, {"ok": False, "error": f"timeout ({timeout_s}s)"})
                finally:
                    lock.release()

            return _json_response(self, 404, {"ok": False, "error": "not found"})

    return Handler


def run_web_ui(settings: Settings) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    # No Windows, 8080 costuma estar bloqueada/reservada (WinError 10013) em alguns setups.
    # Default alto evita surpresa; ainda dá para sobrescrever com WEB_PORT/SERVE_PORT.
    start_port = int(os.getenv("WEB_PORT", os.getenv("SERVE_PORT", "18080")))
    host = os.getenv("WEB_HOST", "127.0.0.1")

    handler = make_handler(settings, repo_root=repo_root)

    # Ajuda em Windows quando a porta está ocupada/reservada (WinError 10013).
    ThreadingHTTPServer.allow_reuse_address = True  # type: ignore[misc]

    last_err: Optional[OSError] = None
    chosen_port: Optional[int] = None
    httpd: Optional[ThreadingHTTPServer] = None

    max_tries = int(os.getenv("WEB_PORT_TRIES", "40"))
    for i in range(max_tries):
        p = start_port + i
        try:
            httpd = ThreadingHTTPServer((host, p), handler)
            chosen_port = p
            break
        except OSError as e:
            last_err = e
            continue

    if httpd is None or chosen_port is None:
        raise RuntimeError(
            "Não consegui abrir a Web UI em nenhuma porta tentada "
            f"({start_port}..{start_port + max_tries - 1}) em {host!r}. "
            "No Windows isso costuma ser porta ocupada/reservada (WinError 10013). "
            "Tente definir WEB_PORT (ex.: 18080) ou libere a porta. "
            f"Último erro: {last_err!r}"
        )

    if chosen_port != start_port:
        print(f"[web] Porta {start_port} indisponível; usando {chosen_port}.")

    print(f"Web UI: http://{host}:{chosen_port}/")
    print(f"Arquivos locais: http://{host}:{chosen_port}/data/ssiptv_consolidated.m3u")
    httpd.serve_forever()
