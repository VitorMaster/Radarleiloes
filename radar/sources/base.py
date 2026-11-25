from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..models import Lot

# Identificação honesta do robô. Sem dados pessoais do operador.
USER_AGENT = "RadarLeiloesBot/0.1 (+monitoramento de editais publicos)"


class Source(ABC):
    """Contrato de uma fonte.

    Regras do projeto (ver docs/fontes.md):
    - preferir feed/arquivo oficial a raspagem de HTML;
    - respeitar termos de uso, robots.txt e limites de acesso;
    - sempre guardar o link da fonte em cada lote;
    - não coletar dados pessoais do executado/devedor.
    """

    id: str
    name: str
    layer: int
    leiloeiro: str | None = None
    verified: bool = False
    scopes: tuple[str, ...] = ()  # ex.: UFs

    @abstractmethod
    def fetch(self, scope: str) -> Iterable[Lot]:
        """Retorna os lotes abertos no escopo (ex.: UF). Deve levantar exceção em falha."""

    def meta(self) -> dict:
        return {"id": self.id, "name": self.name, "layer": self.layer,
                "leiloeiro": self.leiloeiro, "verified": int(self.verified)}
