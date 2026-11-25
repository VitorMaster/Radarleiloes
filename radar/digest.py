"""Resumo diário por perfil: e-mail (HTML) e texto curto para WhatsApp.

Sem SMTP configurado, os resumos são gravados em data/digests/ para conferência.
Variáveis: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
"""
from __future__ import annotations

import os
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .queries import Filters, search

env = Environment(loader=PackageLoader("radar", "templates"), autoescape=select_autoescape())

DISCLAIMER = ("O Radar vende informação e análise. Não intermedeia lances nem cobra comissão sobre "
              "arremate. As notas são apoio à decisão e não substituem a leitura do edital e da matrícula.")


def brl(v) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


env.filters["brl"] = brl


def profile_filters(p: sqlite3.Row, since: str | None) -> Filters:
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else None  # noqa: E731
    return Filters(
        category=p["category"], ufs=split(p["ufs"]), cities=split(p["cities"]),
        min_discount=p["min_discount_pct"], max_price=p["max_price"],
        max_risk=p["max_risk"], min_grade=p["min_grade"], since=since, limit=30,
    )


def build_digest(conn: sqlite3.Connection, profile: sqlite3.Row, hours: int = 24) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    lots = search(conn, profile_filters(profile, since))
    html = env.get_template("digest.html").render(profile=profile, lots=lots, disclaimer=DISCLAIMER,
                                                  today=datetime.now().strftime("%d/%m/%Y"))
    lines = [f"*Radar de Leilões* — {len(lots)} novidade(s) para {profile['name']}"]
    for l in lots[:8]:
        grade = l.get("grade") or "?"
        risk = l.get("risk_level") or "?"
        disc = f"{l['discount_pct']:.0f}%" if l.get("discount_pct") is not None else "—"
        lines.append(f"• {l['title']} — {brl(l['min_bid'])} (-{disc}) | conservação {grade} | risco {risk}\n  {l['url']}")
    return {"lots": lots, "html": html, "whatsapp": "\n".join(lines)}


def send_email(to: str, subject: str, html: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")), to
    msg.set_content("Seu cliente de e-mail não exibe HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def run_digests(conn: sqlite3.Connection, out_dir: str | Path = "data/digests", hours: int = 24) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    results = []
    for p in conn.execute("SELECT * FROM profiles WHERE active=1"):
        d = build_digest(conn, p, hours)
        if not d["lots"]:
            results.append({"profile": p["name"], "lots": 0, "sent": False})
            continue
        sent = bool(p["email"]) and send_email(p["email"], f"Radar de Leilões — {len(d['lots'])} novidade(s)", d["html"])
        base = out / f"{stamp}_perfil{p['id']}"
        base.with_suffix(".html").write_text(d["html"], encoding="utf-8")
        base.with_suffix(".txt").write_text(d["whatsapp"], encoding="utf-8")
        results.append({"profile": p["name"], "lots": len(d["lots"]), "sent": sent, "file": str(base) + ".html"})
    return results
