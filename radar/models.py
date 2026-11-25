"""Registro normalizado de lote, independente da fonte."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Literal

Category = Literal["imovel", "veiculo"]


@dataclass
class ImovelInfo:
    property_type: str | None = None
    area_total_m2: float | None = None
    area_privativa_m2: float | None = None
    bedrooms: int | None = None
    occupied: bool | None = None
    judicial: bool | None = None
    accepts_financing: bool | None = None
    iptu_debt: float | None = None
    condo_debt: float | None = None
    encumbrances: str | None = None


@dataclass
class VeiculoInfo:
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    documentation: str | None = None
    origin: str | None = None
    runs: bool | None = None
    has_key: bool | None = None
    classification: str | None = None


@dataclass
class Lot:
    source_id: str
    external_id: str
    category: Category
    url: str
    title: str | None = None
    leiloeiro: str | None = None
    sale_type: str | None = None
    praca: int | None = None
    auction_date: str | None = None
    appraisal_value: float | None = None
    min_bid: float | None = None
    city: str | None = None
    uf: str | None = None
    neighborhood: str | None = None
    address: str | None = None
    description: str | None = None
    imovel: ImovelInfo | None = None
    veiculo: VeiculoInfo | None = None
    photos: list[str] = field(default_factory=list)

    @property
    def discount_pct(self) -> float | None:
        if self.appraisal_value and self.min_bid is not None and self.appraisal_value > 0:
            return round((1 - self.min_bid / self.appraisal_value) * 100, 2)
        return None

    def content_hash(self) -> str:
        """Hash do conteúdo que importa para a análise (não muda com a data de captura)."""
        payload = {
            "title": self.title, "sale_type": self.sale_type, "praca": self.praca,
            "auction_date": self.auction_date, "appraisal": self.appraisal_value,
            "min_bid": self.min_bid, "description": self.description,
            "imovel": asdict(self.imovel) if self.imovel else None,
            "veiculo": asdict(self.veiculo) if self.veiculo else None,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
