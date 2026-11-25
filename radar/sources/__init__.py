"""Conectores de fontes. Cada fonte implementa `Source.fetch(scope)`."""
from __future__ import annotations

from .base import Source
from .caixa import CaixaSource

REGISTRY: dict[str, type[Source]] = {
    CaixaSource.id: CaixaSource,
}


def get_source(source_id: str) -> Source:
    try:
        return REGISTRY[source_id]()
    except KeyError:
        raise SystemExit(f"Fonte desconhecida: {source_id}. Disponíveis: {', '.join(REGISTRY)}")
