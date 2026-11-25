"""Fonte 1 — Caixa Econômica Federal (camada 1, imóveis).

A Caixa publica um arquivo CSV oficial por UF com todos os imóveis à venda
(venda direta, licitação e leilões SFI). É a melhor primeira fonte possível:
volume alto, arquivo estruturado, sem raspagem de HTML.

Formato observado (latin-1, separador ';'):
  linhas de título, depois o cabeçalho
  N° do imóvel;UF;Cidade;Bairro;Endereço;Preço;Valor de avaliação;Desconto;
  Descrição;Modalidade de venda;Link de acesso
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Iterable

from ..models import ImovelInfo, Lot
from ..vehicles import pretty_city
from .base import Source

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_{uf}.csv"
DETAIL_URL = "https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp?hdnimovel={id}"
PHOTO_URL = "https://venda-imoveis.caixa.gov.br/fotos/F{id:0>13}21.jpg"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_brl(value: str | None) -> float | None:
    """'150.000,00' -> 150000.0 ; '' -> None"""
    if not value:
        return None
    v = value.strip().replace("R$", "").replace(" ", "")
    if not v:
        return None
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


_AREA_RE = re.compile(r"([\d.,]+)\s*de\s*[áa]rea\s*(total|privativa|do terreno)", re.I)
_QTO_RE = re.compile(r"(\d+)\s*qto", re.I)


def parse_description(desc: str) -> ImovelInfo:
    info = ImovelInfo()
    if not desc:
        return info
    first = desc.split(",")[0].strip()
    info.property_type = first.lower() or None
    for num, kind in _AREA_RE.findall(desc):
        try:
            val = parse_brl(num) if "," in num else float(num)
        except ValueError:
            val = None
        if not val:
            continue
        k = kind.lower()
        if k == "privativa":
            info.area_privativa_m2 = val
        elif k == "total" or (k == "do terreno" and info.area_total_m2 is None):
            info.area_total_m2 = val
    m = _QTO_RE.search(desc)
    if m:
        info.bedrooms = int(m.group(1))
    return info


class CaixaSource(Source):
    id = "caixa"
    name = "Caixa Econômica Federal — imóveis"
    layer = 1
    leiloeiro = "Caixa Econômica Federal"
    verified = True

    def download(self, uf: str) -> str:
        return self.get(CSV_URL.format(uf=uf.upper())).content.decode("latin-1")

    def fetch(self, scope: str) -> Iterable[Lot]:
        return list(self.parse(self.download(scope), scope))

    def parse(self, text: str, uf: str | None = None) -> Iterable[Lot]:
        lines = text.splitlines()
        header_idx = next(
            (i for i, line in enumerate(lines) if re.match(r"n\w* (do )?imovel", _norm(line.split(";")[0]))), None
        )
        if header_idx is None:
            raise ValueError("Cabeçalho do CSV da Caixa não encontrado — o formato mudou?")
        reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])), delimiter=";")
        header = [_norm(h) for h in next(reader)]

        def col(row: list[str], *names: str) -> str:
            for name in names:
                for i, h in enumerate(header):
                    if h.startswith(name) and i < len(row):
                        return row[i].strip()
            return ""

        for row in reader:
            if not row or not row[0].strip():
                continue
            ext_id = re.sub(r"\D", "", col(row, "n do imovel", "n imovel", "numero")) or row[0].strip()
            desc = col(row, "descricao")
            sale_type = col(row, "modalidade")
            info = parse_description(desc)
            # Imóveis da Caixa vêm de consolidação de alienação fiduciária: venda extrajudicial.
            info.judicial = False
            fin = _norm(col(row, "financiamento"))
            info.accepts_financing = True if fin == "sim" else False if fin in ("nao", "n") else None
            link = col(row, "link") or DETAIL_URL.format(id=ext_id)
            city = pretty_city(col(row, "cidade")) or None
            yield Lot(
                source_id=self.id,
                external_id=ext_id,
                category="imovel",
                url=link,
                title=f"{(info.property_type or 'Imóvel').capitalize()} em {city or '?'}",
                leiloeiro=self.leiloeiro,
                comitente="Caixa Econômica Federal",
                photo_url=PHOTO_URL.format(id=ext_id),
                sale_type=sale_type or None,
                appraisal_value=parse_brl(col(row, "valor de avaliacao")),
                min_bid=parse_brl(col(row, "preco")),
                city=city,
                uf=(col(row, "uf") or uf or "").upper() or None,
                neighborhood=pretty_city(col(row, "bairro")) or None,
                address=col(row, "endereco") or None,
                description=desc or None,
                imovel=info,
            )
