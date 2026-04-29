from __future__ import annotations

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


def _index_html() -> str:
    # UI simples (sem frameworks) — propositalmente “feio”, mas funcional.
    return """<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>bot_Iptv — painel</title>
  <style>
    body { font-family: Segoe UI, Arial, sans-serif; margin: 16px; color: #111; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
    button { padding: 8px 10px; cursor: pointer; }
    select, input { padding: 8px; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; font-size: 13px; }
    th { background: #f6f6f6; text-align: left; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0b1020; color: #e8ecff; padding: 12px; border-radius: 8px; max-height: 420px; overflow: auto; }
    .muted { color: #666; font-size: 12px; }
    .ok { color: #0a7; font-weight: 600; }
    .bad { color: #b00; font-weight: 600; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  </style>
</head>
<body>
  <h2>bot_Iptv — painel simples</h2>
  <p class="muted">Isso roda localmente. Use com cuidado: URLs podem conter credenciais.</p>

  <div class="row">
    <button id="btnCollect">Coletar (collect)</button>
    <button id="btnRevalidate">Revalidar (revalidate)</button>
    <button id="btnRunOnce">Rodar tudo (run-once)</button>
    <span id="jobStatus" class="muted"></span>
  </div>

  <div class="row" style="margin-top: 12px;">
    <label>Filtrar status
      <select id="statusFilter">
        <option value="all">todos</option>
        <option value="valid">valid</option>
        <option value="invalid">invalid</option>
        <option value="unknown">unknown</option>
      </select>
    </label>
    <button id="btnReload">Recarregar lista</button>
    <span id="counts" class="muted"></span>
  </div>

  <table>
    <thead>
      <tr>
        <th>status</th>
        <th>url</th>
        <th>canais</th>
        <th>última checagem</th>
        <th>ação</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>

  <h3>Preview da lista (M3U)</h3>
  <div class="row">
    <input id="previewUrl" class="mono" style="flex:1; min-width: 320px;" placeholder="cole a URL aqui" />
    <button id="btnPreview">Carregar preview</button>
  </div>
  <pre id="preview" class="mono"></pre>

<script>
async function runJob(cmd) {
  const s = document.getElementById('jobStatus');
  s.textContent = 'Executando: ' + cmd + ' ...';
  const r = await fetch('/api/run/' + encodeURIComponent(cmd), { method: 'POST' });
  const j = await r.json();
  if (!j.ok) {
    s.innerHTML = '<span class="bad">Falhou (' + cmd + ')</span> — ' + (j.error || 'erro');
    if (j.stderr) s.innerHTML += '<div class="mono muted">' + (j.stderr || '').slice(0, 2000) + '</div>';
    return;
  }
  s.innerHTML = '<span class="ok">OK</span> — ' + cmd + ' (exit ' + j.exit_code + ')';
  await loadLists();
}

async function loadLists() {
  const st = document.getElementById('statusFilter').value;
  const r = await fetch('/api/lists?status=' + encodeURIComponent(st));
  const j = await r.json();
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';
  const items = j.items || [];
  document.getElementById('counts').textContent = 'Itens: ' + items.length;

  for (const it of items) {
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    td1.textContent = it.status || '';
    const td2 = document.createElement('td');
    td2.className = 'mono';
    td2.textContent = it.url || '';
    const td3 = document.createElement('td');
    td3.textContent = String(it.channels_count ?? '');
    const td4 = document.createElement('td');
    td4.textContent = it.last_checked || '';
    const td5 = document.createElement('td');
    const b = document.createElement('button');
    b.textContent = 'Preview';
    b.onclick = () => {
      document.getElementById('previewUrl').value = it.url || '';
      preview();
    };
    td5.appendChild(b);
    tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4); tr.appendChild(td5);
    tbody.appendChild(tr);
  }
}

async function preview() {
  const url = document.getElementById('previewUrl').value.trim();
  const pre = document.getElementById('preview');
  pre.textContent = 'Carregando...';
  const r = await fetch('/api/m3u_preview?url=' + encodeURIComponent(url));
  const t = await r.text();
  pre.textContent = t;
}

document.getElementById('btnCollect').onclick = () => runJob('collect');
document.getElementById('btnRevalidate').onclick = () => runJob('revalidate');
document.getElementById('btnRunOnce').onclick = () => runJob('run-once');
document.getElementById('btnReload').onclick = () => loadLists();
document.getElementById('statusFilter').onchange = () => loadLists();
document.getElementById('btnPreview').onclick = () => preview();

loadLists();
</script>
</body>
</html>
"""


def make_handler(settings: Settings, repo_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # menos ruído no console
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path or "/"

            if route in ("/", "/index.html"):
                return _html_response(self, 200, _index_html())

            if route == "/api/lists":
                qs = urllib.parse.parse_qs(parsed.query or "")
                status = (qs.get("status", ["all"])[0] or "all").lower()
                items = _read_lists(settings.db_json)
                if status != "all":
                    items = [it for it in items if str(it.get("status", "")).lower() == status]
                return _json_response(self, 200, {"ok": True, "items": items})

            if route == "/api/m3u_preview":
                qs = urllib.parse.parse_qs(parsed.query or "")
                url = (qs.get("url", [""])[0] or "").strip()
                if not (url.startswith("http://") or url.startswith("https://")):
                    return _text_response(self, 400, "URL inválida", "text/plain; charset=utf-8")
                code, txt = _fetch_m3u_preview_sync(url, float(settings.http_timeout_seconds), max_chars=250_000)
                return _text_response(self, code, txt, "text/plain; charset=utf-8")

            if route.startswith("/data/"):
                rel = route[len("/data/") :].lstrip("/")
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
