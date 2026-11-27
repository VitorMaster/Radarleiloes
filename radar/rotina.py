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
        # fim (não início) da última rotina: os lotes dela são gravados durante a captura,
        # depois de started_at; usar o início os contaria de novo como novidade
        row = conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM routine_runs WHERE ok = 1").fetchone()
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
        lines.append(f"Novidades: {len(ch['new_items'])} · quedas de preço: {len(ch['drops'])} · "
                     f"encerrados: {len(ch['closed'])}")
    if rep.get("radar") == "veiculos":
        lines.append(f"Na sua região: {k['automoveis']} automóveis e {k['motos']} motos em leilão.")
    elif rep.get("radar") == "eletronicos":
        lines.append(f"Eletrônicos novos em leilão: {k.get('el_total', 0)}.")
    else:
        lines.append(f"Na sua área: {k['total']} imóveis ({k['d40']} com 40%+ de desconto).")
    for h in rep["highlights"][:6]:
        lines.append(f"- {h['title']} ({h.get('city') or '—'}/{h.get('uf') or '—'}): R$ {(h['min_bid'] or 0):,.2f}".replace(",", "X")
                     .replace(".", ",").replace("X", ".") + f" — {h['url']}")
    lines += ["", "O Radar vende informação e análise; não intermedeia lances. Confira sempre o edital oficial."]
    return "\n".join(lines)


RADARS = ("imoveis", "veiculos", "eletronicos")


def recipient(radar: str) -> str:
    """Destinatário de cada radar: RADAR_EMAIL_IMOVEIS / _VEICULOS / _ELETRONICOS, ou RADAR_EMAIL."""
    import os
    return (os.environ.get(f"RADAR_EMAIL_{radar.upper()}") or os.environ.get("RADAR_EMAIL")
            or os.environ.get("RADAR_CONTACT_EMAIL", ""))


def run(conn: sqlite3.Connection, out_dir: str | Path, mode: str | None = None, capture: bool = True,
        radars: tuple[str, ...] = RADARS, log=print) -> dict | None:
    """Captura todas as fontes UMA vez e gera um relatório/e-mail independente por radar."""
    mode = mode or mode_for()
    if not mode:
        log("Domingo: a rotina não roda.")
        return None
    started = now_iso()
    since = since_for(conn, mode)
    # só uma rotina COM captura marca o ponto de corte da próxima atualização;
    # regerar o relatório sem captura não mexe no histórico de rotinas
    run_id = None
    if capture:
        run_id = conn.execute("INSERT INTO routine_runs (mode, started_at, since) VALUES (?,?,?)",
                              (mode, started, since)).lastrowid
        conn.commit()
    cap = capture_all(conn, log) if capture else {}
    out = Path(out_dir)
    emails = []
    for radar in radars:
        rdir = out / radar
        rep = build_report(conn, rdir, mode=mode, since=since, radar=radar)
        (rdir / "email.txt").write_text(text_body(rep), encoding="utf-8")
        (rdir / "email_min.html").write_text(minify(rep["html"]), encoding="utf-8")
        emails.append({
            "radar": radar, "to": recipient(radar), "subject": rep["subject"],
            "html": str((rdir / "email_min.html").resolve()), "text": str((rdir / "email.txt").resolve()),
            "inline_images": [{"cid": f"att{i}", "path": str(p.resolve()), "sha256": _sha(p), "filename": p.name}
                              for i, p in enumerate(rep["images"])],
            "pdf": ({"path": str(Path(rep["pdf"]).resolve()), "sha256": _sha(Path(rep["pdf"])),
                     "filename": Path(rep["pdf"]).name} if rep["pdf"] else None),
        })
        log(f"[{radar}] {rep['subject']}")
    manifest = {"mode": mode, "since": since, "generated_at": started, "emails": emails, "capture": cap}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"subjects": [e["subject"] for e in emails], "failed_sources": [k for k, v in cap.items() if "error" in v]}
    if run_id is not None:
        conn.execute("UPDATE routine_runs SET finished_at=?, ok=1, summary=? WHERE id=?",
                     (now_iso(), json.dumps(summary, ensure_ascii=False), run_id))
        conn.commit()
    log(f"Modo: {mode} · desde: {since or '—'} · manifesto: {out / 'manifest.json'}")
    return manifest
