"""Acesso ao SQLite. Um arquivo só, fácil de migrar para Postgres depois."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("RADAR_DB", "data/radar.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or DEFAULT_DB)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL") if str(path) != ":memory:" else None
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files("radar").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate(conn)
    conn.executescript(sql)  # recria índices de tabelas reconstruídas
    conn.commit()


MIGRATIONS = {  # colunas acrescentadas depois da 1ª versão do esquema
    "lots": {"comitente": "TEXT", "photo_url": "TEXT", "lot_count": "INTEGER",
             "distance_km": "REAL", "in_area": "INTEGER", "final_price": "REAL"},
    "lot_veiculo": {"vehicle_type": "TEXT", "color": "TEXT", "fuel": "TEXT"},
}


def _rebuild_check(conn: sqlite3.Connection, table: str, old: str, new: str) -> None:
    """Recria a tabela trocando uma restrição CHECK (SQLite não permite alterá-la)."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not row or old not in row[0]:
        return
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(row[0].replace(old, new).replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}__new", 1))
    conn.execute(f"INSERT INTO {table}__new ({', '.join(cols)}) SELECT {', '.join(cols)} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate(conn: sqlite3.Connection) -> None:
    _rebuild_check(conn, "lots", "CHECK (category IN ('imovel','veiculo'))",
                   "CHECK (category IN ('imovel','veiculo','eletronico'))")
    _rebuild_check(conn, "profiles", "CHECK (category IN ('imovel','veiculo'))",
                   "CHECK (category IN ('imovel','veiculo','eletronico'))")
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
