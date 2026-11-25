"""Conectores de fontes. Cada fonte implementa `Source.fetch(scope)`.

Ordem = prioridade: oficiais primeiro, depois leiloeiros com maior cobertura em ES/MG/BA.
"""
from __future__ import annotations

from .base import Source
from .caixa import CaixaSource
from .detran_ba import DetranBASource
from .mega import MegaSource
from .zuk import ZukSource

REGISTRY: dict[str, type[Source]] = {
    CaixaSource.id: CaixaSource,
    ZukSource.id: ZukSource,
    MegaSource.id: MegaSource,
    DetranBASource.id: DetranBASource,
}


def get_source(source_id: str) -> Source:
    try:
        return REGISTRY[source_id]()
    except KeyError:
        raise SystemExit(f"Fonte desconhecida: {source_id}. Disponíveis: {', '.join(REGISTRY)}")
