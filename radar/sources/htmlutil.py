"""Utilidades mínimas para ler HTML de páginas públicas (sem dependências externas)."""
from __future__ import annotations

import html
import re

from .caixa import parse_brl

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
MONEY_RE = re.compile(r"R\$\s*([\d.]+,\d{2})")
DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s*(?:às|as)?\s*(\d{2}):(\d{2}))?")


def text(fragment: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", fragment))).strip()


def money_values(fragment: str) -> list[float]:
    return [v for v in (parse_brl(m) for m in MONEY_RE.findall(fragment)) if v is not None]


def iso_dates(fragment: str) -> list[str]:
    out = []
    for d, m, y, hh, mm in DATE_RE.findall(fragment):
        out.append(f"{y}-{m}-{d}" + (f"T{hh}:{mm}" if hh else ""))
    return out


def split_blocks(page: str, marker: str) -> list[str]:
    """Divide a página em blocos que começam em `marker` (ex.: a classe do card)."""
    idx = [m.start() for m in re.finditer(re.escape(marker), page)]
    return [page[a:b] for a, b in zip(idx, idx[1:] + [len(page)])]


def page_lines(page: str) -> list[str]:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    return [t for t in (text(x) for x in re.split(r"<[^>]+>", body)) if t]


# Sinais jurídicos contados no texto da matrícula/edital. Guardamos só a contagem,
# nunca nomes de partes (LGPD).
LEGAL_SIGNALS = {
    "penhora": r"penhor",
    "indisponibilidade": r"indisponibilidade",
    "hipoteca": r"hipotec",
    "usufruto": r"usufruto",
    "ação judicial": r"a[çc][ãa]o (judicial|anulat|revisional|possess)",
    "arrolamento": r"arrolamento",
}


CONCRETE = re.compile(r"\bconsta|averba|\bregistr|\bav\.\s*\d|\br\.\s*\d|processo|autos|n[ºo°]\s*\d")
GENERIC = re.compile(r"eventua|\bcaso\b|\bse o im|hip[óo]tese|poder[áa]|responsabiliza|artigo \d+ do cpc")


def legal_signal_summary(text_block: str) -> list[str]:
    """Conta sinais só em frases concretas (que citam matrícula, averbação, processo),
    ignorando cláusulas genéricas de contrato ("caso haja penhora...")."""
    counts: dict[str, int] = {}
    for sentence in re.split(r"(?<=[.;])\s+", text_block.lower()):
        if not CONCRETE.search(sentence) or GENERIC.search(sentence):
            continue
        for label, pat in LEGAL_SIGNALS.items():
            n = len(re.findall(pat, sentence))
            if n:
                counts[label] = counts.get(label, 0) + n
    if not counts:
        return []
    return ["Sinais na matrícula: " + ", ".join(f"{k} ×{v}" for k, v in counts.items())]


def strip_names(s: str) -> str:
    """Remove trechos típicos com nomes de partes: 'requerida por X contra Y', 'de FULANO'."""
    s = re.sub(r"(requerid[oa] por|movid[oa] por|em face de|contra)\s+[^,.;]+", r"\1 [parte omitida]", s, flags=re.I)
    s = re.sub(r"(herdeiros de|propriedade de|executad[oa]:?)\s+[^,.;]+", r"\1 [nome omitido]", s, flags=re.I)
    return s
