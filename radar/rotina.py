"""Rotina diária do Radar.

- Segunda-feira: VARREDURA COMPLETA — captura todas as fontes (com páginas de detalhe e
  anexos dos editais) e envia o panorama completo, com PDF.
- Terça a sexta: ATUALIZAÇÃO — captura e envia só o que mudou desde o último envio
  (imóveis novos, quedas de preço, veículos novos, lotes encerrados/arrematados).
- Sábado: RESUMO SEMANAL — compila a semana (desde segunda 00h) e envia com PDF.
- Domingo: não roda.

Gera `manifest.json` na pasta de saída: assunto, HTML, texto, fotos inline e PDF.
O envio é feito por SMTP (se configurado) ou pelo agente agendado via Gmail.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import now_iso
from .pipeline import run_capture
from .report import build_report
from .sources import REGISTRY

TZ = ZoneInfo("America/Sao_Paulo")
WEEKDAY_MODE = {0: "completo", 1: "atualizacao", 2: "atualizacao", 3: "atualizacao", 4: "atualizacao", 5: "semanal"}


def mode_for(dt: datetime | None = None) -> str | None:
    dt = (dt or datetime.now(TZ)).astimezone(TZ)
    return WEEKDAY_MODE.get(dt.weekday())


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def since_for(conn: sqlite3.Connection, mode: str, now: datetime | None = None) -> str | None:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if mode == "semanal":
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return _utc(monday)
    if mode == "atualizacao":
        row = conn.execute("SELECT MAX(started_at) FROM routine_runs WHERE ok = 1").fetchone()
        return row[0] or _utc(now - timedelta(hours=26))
    return None


def capture_all(conn: sqlite3.Connection, log=print) -> dict:
    """Captura todas as fontes e UFs. Uma fonte com falha não interrompe as outras."""
    result = {}
    for sid, cls in REGISTRY.items():
        source = cls()
        for uf in source.scopes:
            try:
                st = run_capture(conn, source, uf)
                result[f"{sid}/{uf}"] = st
                log(f"[{sid}/{uf}] vistos={st['seen']} novos={st['new']} alterados={st['changed']} "
                    f"encerrados={st['closed']}" + (f"  ⚠ {st['alert']}" if st.get("alert") else ""))
            except Exception as exc:  # noqa: BLE001
                result[f"{sid}/{uf}"] = {"error": str(exc)}
                log(f"[{sid}/{uf}] FALHOU: {exc}")
    return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def minify(html: str) -> str:
    """HTML compacto para o envio (remove comentários e espaços entre tags)."""
    import re
    html = re.sub(r"<!--(?!\[).*?-->", "", html, flags=re.S)
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    return re.sub(r"\n\s*", "\n", html).replace("</tr>", "</tr>\n")


def text_body(rep: dict) -> str:
    """Versão em texto simples do e-mail (alternativa ao HTML)."""
    k, ch = rep["kpis"], rep.get("changes")
    lines = [rep["subject"], ""]
    if ch:
        lines.append(f"Imóveis novos: {len(ch['new_props'])} · quedas de preço: {len(ch['drops'])} · "
                     f"veículos novos: {sum(len(v) for v in ch['new_veh'].values())} · encerrados: {len(ch['closed'])}")
    lines.append(f"Na sua área: {k['total']} imóveis ({k['d40']} com 40%+ de desconto), "
                 f"{k['motos']} motos e {k['automoveis']} automóveis.")
    for h in rep["highlights"][:6]:
        lines.append(f"- {h['title']} ({h['city']}/{h['uf']}): R$ {h['min_bid']:,.2f}".replace(",", "X")
                     .replace(".", ",").replace("X", ".") + f" — {h['url']}")
    lines += ["", "O Radar vende informação e análise; não intermedeia lances. Confira sempre o edital oficial."]
    return "\n".join(lines)


def run(conn: sqlite3.Connection, out_dir: str | Path, mode: str | None = None, capture: bool = True,
        log=print) -> dict | None:
    mode = mode or mode_for()
    if not mode:
        log("Domingo: a rotina não roda.")
        return None
    started = now_iso()
    since = since_for(conn, mode)
    run_id = conn.execute("INSERT INTO routine_runs (mode, started_at, since) VALUES (?,?,?)",
                          (mode, started, since)).lastrowid
    conn.commit()
    cap = capture_all(conn, log) if capture else {}
    rep = build_report(conn, out_dir, mode=mode, since=since)
    out = Path(out_dir)
    (out / "email.txt").write_text(text_body(rep), encoding="utf-8")
    (out / "email_min.html").write_text(minify(rep["html"]), encoding="utf-8")
    manifest = {
        "mode": mode, "subject": rep["subject"], "since": since, "generated_at": started,
        "html": str((out / "email_min.html").resolve()), "text": str((out / "email.txt").resolve()),
        "inline_images": [{"cid": f"att{i}", "path": str(p.resolve()), "sha256": _sha(p), "filename": p.name}
                          for i, p in enumerate(rep["images"])],
        "pdf": ({"path": str(Path(rep["pdf"]).resolve()), "sha256": _sha(Path(rep["pdf"])),
                 "filename": Path(rep["pdf"]).name} if rep["pdf"] else None),
        "capture": cap,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"subject": rep["subject"], "failed_sources": [k for k, v in cap.items() if "error" in v]}
    conn.execute("UPDATE routine_runs SET finished_at=?, ok=1, summary=? WHERE id=?",
                 (now_iso(), json.dumps(summary, ensure_ascii=False), run_id))
    conn.commit()
    log(f"Modo: {mode} · desde: {since or '—'}\nAssunto: {rep['subject']}\nManifesto: {out / 'manifest.json'}")
    return manifest
