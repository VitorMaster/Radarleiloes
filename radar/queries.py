"""Consultas usadas pela web, pelo resumo diário e pela CLI."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
RISK_ORDER = {"baixo": 0, "medio": 1, "alto": 2}

BASE = """
SELECT l.*, s.name AS source_name, s.verified AS source_verified,
       c.grade, c.justification, c.evidence, c.method AS grade_method,
       r.level AS risk_level, r.reasons AS risk_reasons, r.needs_review, r.human_reviewed,
       i.property_type, i.area_total_m2, i.area_privativa_m2, i.bedrooms, i.occupied, i.judicial,
       v.brand, v.model, v.year, v.classification
FROM lots l
JOIN sources s ON s.id = l.source_id
LEFT JOIN conservation c ON c.lot_id = l.id
LEFT JOIN legal_risk r ON r.lot_id = l.id
LEFT JOIN lot_imovel i ON i.lot_id = l.id
LEFT JOIN lot_veiculo v ON v.lot_id = l.id
"""


@dataclass
class Filters:
    category: str | None = None
    ufs: list[str] | None = None
    cities: list[str] | None = None
    min_discount: float | None = None
    max_price: float | None = None
    max_risk: str | None = None
    min_grade: str | None = None
    status: str = "aberto"
    since: str | None = None   # first_seen_at >= since (para "novidades do dia")
    q: str | None = None
    order: str = "desconto"
    limit: int = 100
    offset: int = 0


def search(conn: sqlite3.Connection, f: Filters) -> list[dict]:
    where, args = ["l.status = ?"], [f.status]
    if f.category:
        where.append("l.category = ?"); args.append(f.category)
    if f.ufs:
        where.append(f"l.uf IN ({','.join('?' * len(f.ufs))})"); args += f.ufs
    if f.cities:
        where.append(f"lower(l.city) IN ({','.join('?' * len(f.cities))})"); args += [c.lower() for c in f.cities]
    if f.min_discount is not None:
        where.append("l.discount_pct >= ?"); args.append(f.min_discount)
    if f.max_price is not None:
        where.append("l.min_bid <= ?"); args.append(f.max_price)
    if f.max_risk in RISK_ORDER:
        allowed = [k for k, v in RISK_ORDER.items() if v <= RISK_ORDER[f.max_risk]]
        where.append(f"r.level IN ({','.join('?' * len(allowed))})"); args += allowed
    if f.min_grade in GRADE_ORDER:
        allowed = [k for k, v in GRADE_ORDER.items() if v <= GRADE_ORDER[f.min_grade]]
        where.append(f"c.grade IN ({','.join('?' * len(allowed))})"); args += allowed
    if f.since:
        where.append("l.first_seen_at >= ?"); args.append(f.since)
    if f.q:
        where.append("(l.title LIKE ? OR l.description LIKE ? OR l.city LIKE ? OR l.neighborhood LIKE ?)")
        args += [f"%{f.q}%"] * 4
    order = {
        "desconto": "l.discount_pct DESC NULLS LAST",
        "preco": "l.min_bid ASC NULLS LAST",
        "recentes": "l.first_seen_at DESC",
        "data": "l.auction_date ASC NULLS LAST",
    }.get(f.order, "l.discount_pct DESC NULLS LAST")
    sql = f"{BASE} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?"
    return [_decorate(dict(r)) for r in conn.execute(sql, [*args, f.limit, f.offset])]


def get_lot(conn: sqlite3.Connection, lot_id: int) -> dict | None:
    row = conn.execute(f"{BASE} WHERE l.id = ?", (lot_id,)).fetchone()
    if not row:
        return None
    lot = _decorate(dict(row))
    lot["history"] = [dict(h) for h in conn.execute(
        "SELECT * FROM lot_observations WHERE lot_id=? ORDER BY observed_at", (lot_id,))]
    return lot


def _decorate(d: dict) -> dict:
    for key in ("evidence", "risk_reasons"):
        try:
            d[key] = json.loads(d[key]) if d.get(key) else []
        except (TypeError, ValueError):
            d[key] = []
    return d


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(status='aberto') abertos,
                  SUM(status='encerrado') encerrados,
                  AVG(CASE WHEN status='aberto' THEN discount_pct END) desconto_medio
           FROM lots"""
    ).fetchone()
    by_uf = conn.execute(
        "SELECT uf, category, COUNT(*) n FROM lots WHERE status='aberto' GROUP BY uf, category ORDER BY uf"
    ).fetchall()
    graded = conn.execute("SELECT grade, COUNT(*) n FROM conservation GROUP BY grade").fetchall()
    return {**dict(row), "by_uf": [dict(r) for r in by_uf], "graded": {r["grade"]: r["n"] for r in graded}}
