from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Iterable

import requests

from ..models import Lot

# Identifica o robô de forma honesta, sem dados pessoais do operador.
USER_AGENT = "Mozilla/5.0 (compatible; RadarLeiloesBot/0.2; +monitoramento de editais publicos)"


class Source(ABC):
    """Contrato de uma fonte.

    Regras do projeto (ver docs/fontes.md):
    - preferir feed/arquivo oficial a raspagem de HTML;
    - respeitar termos de uso, robots.txt e limites de acesso (1 req/s);
    - não contornar tokens anti-robô nem áreas logadas;
    - sempre guardar o link da fonte em cada lote;
    - não coletar dados pessoais do executado/devedor.
    """

    id: str
    name: str
    layer: int
    leiloeiro: str | None = None
    verified: bool = False
    scopes: tuple[str, ...] = ("ES", "MG", "BA")
    delay_s: float = 1.0

    def __init__(self, session: requests.Session | None = None, timeout: int = 60):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout
        self._last = 0.0

    def get(self, url: str, **kw) -> requests.Response:
        wait = self.delay_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(url, timeout=self.timeout, **kw)
        self._last = time.monotonic()
        r.raise_for_status()
        return r

    @abstractmethod
    def fetch(self, scope: str) -> Iterable[Lot]:
        """Retorna os lotes abertos no escopo (ex.: UF). Deve levantar exceção em falha."""

    def meta(self) -> dict:
        return {"id": self.id, "name": self.name, "layer": self.layer,
                "leiloeiro": self.leiloeiro, "verified": int(self.verified)}
