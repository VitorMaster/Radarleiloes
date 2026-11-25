"""Nota de conservação (A–D) extraída por IA do texto do edital e das fotos.

Princípios:
- Toda nota vem com justificativa em uma frase e evidências (trechos/fotos).
- Sem informação suficiente => 'sem_info', nunca um palpite.
- Só reavalia quando o conteúdo do lote muda (content_hash).
"""
from __future__ import annotations

import json
import os
import sqlite3

from .db import now_iso

MODEL = os.environ.get("RADAR_MODEL", "claude-opus-5-5")

RUBRIC = {
    "imovel": {
        "A": "Pronto para uso ou locação",
        "B": "Pequenas reformas",
        "C": "Reforma grande ou pouca informação sobre o estado",
        "D": "Ruína, invasão ou dano estrutural",
    },
    "veiculo": {
        "A": "Roda, documentado, sem avarias relevantes",
        "B": "Roda, avarias leves ou documentação regular",
        "C": "Recuperável, sinistro médio, sem chave",
        "D": "Sucata ou não roda",
    },
}

SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "sem_info"]},
        "justification": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grade", "justification", "evidence"],
    "additionalProperties": False,
}

SYSTEM = """Você avalia o estado de conservação de bens em leilão no Brasil para investidores.
Use somente o que está no texto do edital/anúncio e nas fotos fornecidas. Não invente.

Escala para {category}:
{rubric}
sem_info: o texto e as fotos não permitem avaliar o estado físico.

Regras:
- Dados de cadastro (área, quartos, endereço, preço) NÃO dizem nada sobre conservação.
  Se só houver isso, responda sem_info.
- justification: uma frase, em português, dizendo por que a nota foi dada.
- evidence: trechos copiados literalmente do edital (entre aspas) ou "foto N: o que se vê".
  Para sem_info, evidence pode ser vazia.
- Na dúvida entre duas notas, escolha a pior e explique."""


def _client():
    import anthropic
    return anthropic.Anthropic()


def grade_lot(lot: sqlite3.Row, photos: list[str] | None = None, client=None) -> dict:
    """Chama o Claude e devolve {'grade','justification','evidence','model'}."""
    client = client or _client()
    rubric = "\n".join(f"{k}: {v}" for k, v in RUBRIC[lot["category"]].items())
    text = (
        f"Título: {lot['title']}\nModalidade: {lot['sale_type']}\n"
        f"Local: {lot['city']}/{lot['uf']} — {lot['neighborhood'] or ''}\n"
        f"Descrição/edital:\n{lot['description'] or '(sem descrição)'}"
    )
    content: list[dict] = [
        {"type": "image", "source": {"type": "url", "url": u}} for u in (photos or [])[:6]
    ]
    content.append({"type": "text", "text": text})

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM.format(category=lot["category"], rubric=rubric),
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return {"grade": "sem_info", "justification": "Avaliação automática indisponível para este lote.",
                "evidence": [], "model": response.model}
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Resposta truncada (max_tokens)")
    raw = next(b.text for b in response.content if b.type == "text")
    data = json.loads(raw)
    data["model"] = response.model
    return data


def pending_lots(conn: sqlite3.Connection, limit: int, uf: str | None = None) -> list[sqlite3.Row]:
    """Lotes abertos sem nota ou cujo conteúdo mudou — maiores descontos primeiro."""
    q = """SELECT l.* FROM lots l LEFT JOIN conservation c ON c.lot_id = l.id
           WHERE l.status='aberto' AND l.lot_count IS NULL AND (c.lot_id IS NULL OR c.content_hash <> l.content_hash)"""
    args: list = []
    if uf:
        q += " AND l.uf=?"
        args.append(uf)
    q += " ORDER BY l.discount_pct DESC NULLS LAST LIMIT ?"
    args.append(limit)
    return conn.execute(q, args).fetchall()


def save_grade(conn: sqlite3.Connection, lot: sqlite3.Row, result: dict, method: str) -> None:
    conn.execute(
        """INSERT INTO conservation (lot_id, grade, justification, evidence, method, content_hash, graded_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(lot_id) DO UPDATE SET grade=excluded.grade, justification=excluded.justification,
             evidence=excluded.evidence, method=excluded.method, content_hash=excluded.content_hash,
             graded_at=excluded.graded_at""",
        (lot["id"], result["grade"], result["justification"],
         json.dumps(result.get("evidence", []), ensure_ascii=False), method,
         lot["content_hash"], now_iso()),
    )
    conn.commit()


def grade_pending(conn: sqlite3.Connection, limit: int = 20, uf: str | None = None, client=None) -> dict:
    import anthropic
    client = client or _client()
    stats = {"graded": 0, "errors": 0}
    for lot in pending_lots(conn, limit, uf):
        try:
            result = grade_lot(lot, client=client)
        except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"  lote {lot['id']}: erro {type(exc).__name__}: {exc}")
            stats["errors"] += 1
            continue
        save_grade(conn, lot, result, f"ia:{result.get('model', MODEL)}")
        stats["graded"] += 1
    return stats
