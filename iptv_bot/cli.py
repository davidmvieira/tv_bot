from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Optional

from .config import load_settings
from .logging_utils import setup_logging, get_logger
from .pipeline import run_once, collect_and_register, validate_all, aggregate_valid
from .storage.json_store import JsonStore
from .scheduler.runner import run_scheduler
from .collector.telegram_collector import check_targets


log = get_logger(__name__)

def _safe_console_text(s: object) -> str:
    """
    Evita crash de encoding no Windows (ex.: emoji em títulos de canais).
    """
    txt = "" if s is None else str(s)
    try:
        return txt.encode("cp1252", errors="replace").decode("cp1252")
    except Exception:
        return txt.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iptv_bot", description="Coleta/valida/agrega listas M3U a partir do Telegram.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run-once", help="Executa coleta + validação + agregação uma vez.")
    sub.add_parser("collect", help="Somente coleta e registra URLs no JSON.")
    sub.add_parser("revalidate", help="Revalida todas as URLs já registradas.")
    sub.add_parser("aggregate", help="Gera a M3U consolidada com listas válidas.")
    sub.add_parser("scheduler", help="Executa o scheduler (cron) em loop.")
    sub.add_parser("serve", help="Servir a pasta data/ via HTTP (endpoint simples).")
    sub.add_parser("web", help="Abre uma UI web simples (painel) para coletar/filtrar/preview.")
    sub.add_parser("test-targets", help="Testa se os TELEGRAM_TARGETS resolvem (acesso/ID/tipo).")
    sub.add_parser("extract-urls", help="Coleta mensagens e apenas lista URLs extraídas (debug).")

    return p


async def _run(cmd: str) -> int:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = JsonStore(settings.db_json)

    if cmd == "run-once":
        await run_once(settings)
        return 0

    if cmd == "collect":
        await collect_and_register(settings, store)
        return 0

    if cmd == "revalidate":
        await validate_all(settings, store)
        return 0

    if cmd == "aggregate":
        await aggregate_valid(settings, store)
        return 0

    if cmd == "scheduler":
        await run_scheduler(settings)
        return 0

    if cmd == "serve":
        import os
        from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

        start_port = int(os.getenv("SERVE_PORT", "8080"))
        host = os.getenv("SERVE_HOST", "0.0.0.0")
        os.chdir(str(settings.data_dir))

        ThreadingHTTPServer.allow_reuse_address = True  # type: ignore[misc]

        last_err: Optional[BaseException] = None
        chosen_port: Optional[int] = None
        httpd: Optional[ThreadingHTTPServer] = None

        max_tries = int(os.getenv("SERVE_PORT_TRIES", "40"))
        for i in range(max_tries):
            p = start_port + i
            try:
                httpd = ThreadingHTTPServer((host, p), SimpleHTTPRequestHandler)
                chosen_port = p
                break
            except OSError as e:
                last_err = e
                continue

        if httpd is None or chosen_port is None:
            raise RuntimeError(
                "Não consegui abrir o servidor estático em nenhuma porta tentada "
                f"({start_port}..{start_port + max_tries - 1}) em {host!r}. "
                f"Último erro: {last_err!r}"
            )

        if chosen_port != start_port:
            log.info("Porta %d indisponível; usando %d.", start_port, chosen_port)

        log.info("Serving %s em http://localhost:%d/ (host=%s)", str(settings.data_dir), chosen_port, host)
        httpd.serve_forever()
        return 0

    if cmd == "web":
        from .webapp.server import run_web_ui

        run_web_ui(settings)
        return 0

    if cmd == "test-targets":
        if settings.telegram_api_id <= 0 or not settings.telegram_api_hash:
            raise RuntimeError("Telegram credentials missing: set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        results = await check_targets(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            session_name=settings.telegram_session,
            targets=settings.telegram_targets,
        )
        ok = 0
        for r in results:
            if r.ok:
                ok += 1
                log.info(
                    "OK target=%s kind=%s title=%s id=%s",
                    _safe_console_text(r.target),
                    _safe_console_text(r.kind),
                    _safe_console_text(r.title),
                    r.resolved_id,
                )
            else:
                log.error("FAIL target=%s error=%s", _safe_console_text(r.target), _safe_console_text(r.error))
        return 0 if ok == len(results) else 1

    if cmd == "extract-urls":
        if settings.telegram_api_id <= 0 or not settings.telegram_api_hash:
            raise RuntimeError("Telegram credentials missing: set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        from .collector.telegram_collector import collect_recent_messages
        from .parser.url_extractor import extract_candidate_urls_from_messages

        msgs = await collect_recent_messages(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            session_name=settings.telegram_session,
            targets=settings.telegram_targets,
            limit_per_target=settings.telegram_limit,
        )
        http_msgs = [m for m in msgs if "http://" in m.lower() or "https://" in m.lower()]
        m3u_msgs = [m for m in msgs if "m3u" in m.lower() or "get.php" in m.lower()]
        urls = extract_candidate_urls_from_messages(msgs)
        log.info(
            "Messages=%d http_msgs=%d m3u_msgs=%d extracted_urls=%d (limit_per_target=%d)",
            len(msgs),
            len(http_msgs),
            len(m3u_msgs),
            len(urls),
            settings.telegram_limit,
        )
        for sample in m3u_msgs[:5]:
            log.info("MSG_SAMPLE %s", _safe_console_text(sample[:180].replace("\n", "\\n")))
        for u in urls[:200]:
            log.info("URL %s", _safe_console_text(u))
        if len(urls) > 200:
            log.info("... (%d more)", len(urls) - 200)
        return 0

    log.error("Unknown command: %s", cmd)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging()
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args.cmd))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

