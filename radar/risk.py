"""Risco jurídico — primeira passada automática por sinais do edital.

Não substitui o advogado: o sistema aponta sinais e marca `needs_review`
nos lotes de maior valor ou com risco alto. A revisão humana sobrescreve.
Os critérios seguem a tabela do plano e devem ser calibrados com editais reais.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

from .models import Lot

REVIEW_VALUE_THRESHOLD = 300_000.0  # lotes acima disso vão para revisão do advogado

# (padrão no texto normalizado, nível, motivo)
TEXT_SIGNALS: list[tuple[str, str, str]] = [
    (r"\bpenhora", "alto", "Menção a penhora no edital"),
    (r"\bacao (judicial|anulatoria|revisional)|\bdisputa judicial|\blitigio", "alto", "Disputa judicial mencionada"),
    (r"\bonus (nao baixad|pendente)|\bgravame", "alto", "Ônus/gravame não baixado"),
    (r"\busufruto", "alto", "Usufruto sobre o imóvel"),
    (r"\binvadid|\binvasao", "alto", "Imóvel invadido"),
    (r"\bocupad", "medio", "Imóvel ocupado — desocupação por conta do comprador"),
    (r"\bdivida(s)? de (iptu|condominio)|\bdebitos? de (iptu|condominio)", "medio", "Dívidas de IPTU/condomínio mencionadas"),
    (r"\brestricao (administrativa|judicial)|\brenajud|\bbloqueio", "medio", "Restrição sobre o bem"),
    (r"\bdesocupad", "baixo", "Imóvel desocupado"),
]

LEVEL_ORDER = {"sem_info": -1, "baixo": 0, "medio": 1, "alto": 2}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def evaluate(lot: Lot) -> tuple[str, list[str], bool]:
    """Retorna (nível, motivos, precisa_revisão)."""
    text = _norm(" ".join(filter(None, [lot.description, lot.sale_type, lot.title])))
    reasons: list[str] = []
    level = "baixo"

    def bump(new: str, why: str):
        nonlocal level
        reasons.append(why)
        if LEVEL_ORDER[new] > LEVEL_ORDER[level]:
            level = new

    for pattern, lvl, why in TEXT_SIGNALS:
        if re.search(pattern, text):
            # "desocupado" contém "ocupad": não contar como ocupado
            if why.startswith("Imóvel ocupado") and re.search(r"\bdesocupad", text):
                continue
            bump(lvl, why)

    im = lot.imovel
    if im:
        if im.judicial:
            bump("medio", "Venda judicial — conferir processo e intimações")
        elif im.judicial is False:
            reasons.append("Venda extrajudicial")
        if im.occupied:
            bump("medio", "Imóvel ocupado")
        if (im.iptu_debt or 0) + (im.condo_debt or 0) > 0.1 * (lot.min_bid or float("inf")):
            bump("medio", "Dívidas acima de 10% do lance mínimo")
        if im.encumbrances:
            bump("alto", f"Ônus na matrícula: {im.encumbrances}")

    if lot.sale_type and "leilao" in _norm(lot.sale_type) and not lot.auction_date and lot.source_id != "caixa":
        reasons.append("Data do leilão não informada")

    # Sem nenhum sinal e sem informação de ocupação: não chutar "baixo".
    informative = [r for r in reasons if r != "Venda extrajudicial"]
    if not informative and (not im or im.occupied is None):
        level = "sem_info"
        reasons.append("Edital não informa ocupação nem ônus — ler a matrícula")

    needs_review = level == "alto" or (lot.min_bid or 0) >= REVIEW_VALUE_THRESHOLD
    return level, reasons, needs_review


def assess_risk(conn: sqlite3.Connection, lot_id: int, lot: Lot, ts: str) -> None:
    existing = conn.execute("SELECT human_reviewed FROM legal_risk WHERE lot_id=?", (lot_id,)).fetchone()
    if existing and existing["human_reviewed"]:
        return  # revisão do advogado prevalece
    level, reasons, review = evaluate(lot)
    conn.execute(
        """INSERT INTO legal_risk (lot_id, level, reasons, needs_review, assessed_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(lot_id) DO UPDATE SET level=excluded.level, reasons=excluded.reasons,
             needs_review=excluded.needs_review, assessed_at=excluded.assessed_at""",
        (lot_id, level, json.dumps(reasons, ensure_ascii=False), int(review), ts),
    )


def record_review(conn: sqlite3.Connection, lot_id: int, level: str, reasons: list[str], reviewer: str) -> None:
    from .db import now_iso
    conn.execute(
        """INSERT INTO legal_risk (lot_id, level, reasons, needs_review, human_reviewed, reviewer, assessed_at)
           VALUES (?,?,?,0,1,?,?)
           ON CONFLICT(lot_id) DO UPDATE SET level=excluded.level, reasons=excluded.reasons,
             needs_review=0, human_reviewed=1, reviewer=excluded.reviewer, assessed_at=excluded.assessed_at""",
        (lot_id, level, json.dumps(reasons, ensure_ascii=False), reviewer, now_iso()),
    )
    conn.commit()
