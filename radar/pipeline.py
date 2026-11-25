"""Captura diária: baixa a fonte, normaliza, grava e registra o histórico.

Para cada execução (fonte + escopo):
- lote novo            -> insere + observação 'novo'
- preço mudou          -> atualiza + observação 'preco_alterado'
- lote sumiu da fonte  -> status 'encerrado' + observação 'sumiu'
  (é o sinal de que foi vendido ou retirado: base do "preço realizado")
- volume caiu > 40%    -> alerta no capture_runs (site mudou? captador quebrou?)
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict
from typing import Iterable

from . import geo
from .db import now_iso
from .models import Lot
from .risk import assess_risk
from .sources.base import Source

VOLUME_DROP_ALERT = 0.40


def ensure_source(conn: sqlite3.Connection, source: Source) -> None:
    m = source.meta()
    conn.execute(
        """INSERT INTO sources (id, name, layer, leiloeiro, verified)
           VALUES (:id, :name, :layer, :leiloeiro, :verified)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, layer=excluded.layer,
             leiloeiro=excluded.leiloeiro, verified=excluded.verified""",
        m,
    )


def _observe(conn, lot_id: int, lot_min_bid, appraisal, status: str, event: str, ts: str):
    conn.execute(
        """INSERT INTO lot_observations (lot_id, observed_at, min_bid, appraisal_value, status, event)
           VALUES (?,?,?,?,?,?)""",
        (lot_id, ts, lot_min_bid, appraisal, status, event),
    )


def _geo(lot: Lot) -> tuple[float | None, int]:
    """Distância até a cidade-base. Editais com vários municípios usam o mais próximo."""
    cities = [lot.city]
    if lot.lot_count and lot.neighborhood:
        cities = [c.strip() for c in re.split(r",| e ", lot.neighborhood) if c.strip()]
    dists = [d for d in (geo.distance_km(c, lot.uf) for c in cities) if d is not None]
    dist = min(dists) if dists else None
    return dist, int(geo.in_area(lot.uf, dist))


def backfill_geo(conn: sqlite3.Connection) -> int:
    """Recalcula distância/área de todos os lotes (ao mudar a cidade-base ou o raio)."""
    n = 0
    for r in conn.execute("SELECT id, city, uf, neighborhood, lot_count FROM lots").fetchall():
        lot = Lot(source_id="", external_id="", category="imovel", url="", city=r["city"], uf=r["uf"],
                  neighborhood=r["neighborhood"], lot_count=r["lot_count"])
        dist, area = _geo(lot)
        conn.execute("UPDATE lots SET distance_km=?, in_area=? WHERE id=?", (dist, area, r["id"]))
        n += 1
    conn.commit()
    return n


def upsert_lot(conn: sqlite3.Connection, lot: Lot, ts: str) -> tuple[int, str | None]:
    """Grava o lote. Retorna (lot_id, evento) onde evento ∈ {novo, preco_alterado, reaberto, alterado, None}."""
    h = lot.content_hash()
    dist, area = _geo(lot)
    row = conn.execute(
        "SELECT id, min_bid, appraisal_value, status, content_hash FROM lots WHERE source_id=? AND external_id=?",
        (lot.source_id, lot.external_id),
    ).fetchone()
    fields = dict(
        category=lot.category, title=lot.title, url=lot.url, leiloeiro=lot.leiloeiro, comitente=lot.comitente,
        sale_type=lot.sale_type, praca=lot.praca, auction_date=lot.auction_date,
        appraisal_value=lot.appraisal_value, min_bid=lot.min_bid, discount_pct=lot.discount_pct,
        city=lot.city, uf=lot.uf, neighborhood=lot.neighborhood, address=lot.address,
        description=lot.description, photo_url=lot.photo_url, lot_count=lot.lot_count, content_hash=h,
        distance_km=dist, in_area=area,
    )
    event: str | None
    if row is None:
        cols = ", ".join(fields)
        cur = conn.execute(
            f"""INSERT INTO lots (source_id, external_id, {cols}, status, first_seen_at, last_seen_at)
                VALUES (?, ?, {", ".join("?" * len(fields))}, 'aberto', ?, ?)""",
            (lot.source_id, lot.external_id, *fields.values(), ts, ts),
        )
        lot_id = cur.lastrowid
        event = "novo"
    else:
        lot_id = row["id"]
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE lots SET {sets}, status='aberto', closed_at=NULL, last_seen_at=? WHERE id=?",
            (*fields.values(), ts, lot_id),
        )
        if row["status"] != "aberto":
            event = "reaberto"
        elif row["min_bid"] != lot.min_bid or row["appraisal_value"] != lot.appraisal_value:
            event = "preco_alterado"
        elif row["content_hash"] != h:
            event = "alterado"
        else:
            event = None

    if event in ("novo", "preco_alterado", "reaberto"):
        _observe(conn, lot_id, lot.min_bid, lot.appraisal_value, "aberto", event, ts)

    if lot.imovel:
        d = asdict(lot.imovel)
        conn.execute(
            f"""INSERT INTO lot_imovel (lot_id, {", ".join(d)}) VALUES (?, {", ".join("?" * len(d))})
                ON CONFLICT(lot_id) DO UPDATE SET {", ".join(f"{k}=excluded.{k}" for k in d)}""",
            (lot_id, *d.values()),
        )
    if lot.veiculo:
        d = asdict(lot.veiculo)
        conn.execute(
            f"""INSERT INTO lot_veiculo (lot_id, {", ".join(d)}) VALUES (?, {", ".join("?" * len(d))})
                ON CONFLICT(lot_id) DO UPDATE SET {", ".join(f"{k}=excluded.{k}" for k in d)}""",
            (lot_id, *d.values()),
        )
    assess_risk(conn, lot_id, lot, ts)  # barato; mantém o risco coerente quando as regras evoluem
    return lot_id, event


def run_capture(conn: sqlite3.Connection, source: Source, scope: str,
                lots: Iterable[Lot] | None = None) -> dict:
    """Executa uma captura. `lots` permite injetar dados (testes/arquivo local)."""
    ensure_source(conn, source)
    ts = now_iso()
    run_id = conn.execute(
        "INSERT INTO capture_runs (source_id, scope, started_at) VALUES (?,?,?)",
        (source.id, scope, ts),
    ).lastrowid
    conn.commit()

    stats = {"seen": 0, "new": 0, "changed": 0, "closed": 0}
    try:
        batch = list(lots) if lots is not None else list(source.fetch(scope))
        if not batch:
            raise RuntimeError("Fonte retornou zero lotes — possível mudança no site ou bloqueio.")
        seen_ids: list[int] = []
        for lot in batch:
            lot_id, event = upsert_lot(conn, lot, ts)
            seen_ids.append(lot_id)
            stats["seen"] += 1
            if event == "novo":
                stats["new"] += 1
            elif event:
                stats["changed"] += 1

        # Lotes abertos desta fonte/escopo que não apareceram hoje: saíram da fonte.
        placeholders = ",".join("?" * len(seen_ids))
        gone = conn.execute(
            f"""SELECT id, min_bid, appraisal_value FROM lots
                WHERE source_id=? AND uf=? AND status='aberto' AND id NOT IN ({placeholders})""",
            (source.id, scope, *seen_ids),
        ).fetchall()
        for g in gone:
            conn.execute("UPDATE lots SET status='encerrado', closed_at=? WHERE id=?", (ts, g["id"]))
            _observe(conn, g["id"], g["min_bid"], g["appraisal_value"], "encerrado", "sumiu", ts)
        stats["closed"] = len(gone)

        alert = _volume_alert(conn, source.id, scope, stats["seen"], run_id)
        conn.execute(
            """UPDATE capture_runs SET finished_at=?, ok=1, lots_seen=?, lots_new=?,
               lots_changed=?, lots_closed=?, alert=? WHERE id=?""",
            (now_iso(), stats["seen"], stats["new"], stats["changed"], stats["closed"], alert, run_id),
        )
        conn.commit()
        stats["alert"] = alert
    except Exception as exc:  # registra a falha para o monitor e repassa
        conn.rollback()
        conn.execute(
            "UPDATE capture_runs SET finished_at=?, ok=0, error=? WHERE id=?",
            (now_iso(), f"{type(exc).__name__}: {exc}", run_id),
        )
        conn.commit()
        raise
    return stats


def _volume_alert(conn, source_id: str, scope: str, seen: int, run_id: int) -> str | None:
    prev = conn.execute(
        """SELECT lots_seen FROM capture_runs WHERE source_id=? AND scope=? AND ok=1 AND id<>?
           ORDER BY id DESC LIMIT 1""",
        (source_id, scope, run_id),
    ).fetchone()
    if prev and prev["lots_seen"] and seen < prev["lots_seen"] * (1 - VOLUME_DROP_ALERT):
        return f"Volume caiu de {prev['lots_seen']} para {seen} lotes — conferir a fonte."
    return None


def health_report(conn: sqlite3.Connection) -> list[dict]:
    """Última execução por fonte/escopo — usado pelo monitor diário."""
    rows = conn.execute(
        """SELECT r.* FROM capture_runs r
           JOIN (SELECT source_id, scope, MAX(id) mid FROM capture_runs GROUP BY source_id, scope) x
             ON x.mid = r.id ORDER BY r.source_id, r.scope"""
    ).fetchall()
    return [dict(r) for r in rows]
