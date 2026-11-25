"""Mega Leilões (camada 1): imóveis e veículos, judiciais e extrajudiciais
(bancos como Itaú e Bradesco, e varas da Justiça). Leiloeiro oficial identificado.

Lê as páginas públicas por UF (robots.txt permite), com paginação ?pagina=N.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..models import ImovelInfo, Lot, VeiculoInfo
from ..vehicles import classify
from .base import Source
from .caixa import parse_brl
from .htmlutil import iso_dates, legal_signal_summary, page_lines, split_blocks, strip_names, text

LIST_URL = "https://www.megaleiloes.com.br/{uf}?pagina={page}"
CATEGORIES = {"imoveis": "imovel", "veiculos": "veiculo"}

BANKS = {
    "itau": "Itaú Unibanco", "bradesco": "Bradesco", "santander": "Santander", "caixa": "Caixa",
    "bb": "Banco do Brasil", "banco-do-brasil": "Banco do Brasil", "safra": "Safra", "inter": "Banco Inter",
    "btg": "BTG Pactual", "sicoob": "Sicoob", "sicredi": "Sicredi", "banrisul": "Banrisul",
}


def _comitente(icon: str | None, judicial: bool) -> str | None:
    if not icon:
        return None
    key = icon.rsplit("/", 1)[-1].replace("leilao-", "").replace("banco-", "").rsplit(".", 1)[0]
    if key in BANKS:
        return BANKS[key]
    if judicial:
        return "Poder Judiciário"
    return None if key == "megaleiloes" else key.replace("-", " ").title()


def _neighborhood(title: str, city: str | None) -> str | None:
    """Título típico: 'Casa 73 m² - Bairro - Cidade - UF'. Só aceita o bairro se não for a cidade."""
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) < 4:
        return None
    hood = parts[1]
    if city and (hood.lower().startswith(city.lower()) or city.lower().startswith(hood.lower())):
        return None
    return hood


class MegaSource(Source):
    id = "mega"
    name = "Mega Leilões — imóveis e veículos"
    layer = 1
    leiloeiro = "Mega Leilões (leiloeiro oficial)"
    verified = True
    max_pages = 10

    def fetch(self, scope: str) -> Iterable[Lot]:
        lots: list[Lot] = []
        page, last = 1, 1
        while page <= min(last, self.max_pages):
            html = self.get(LIST_URL.format(uf=scope.lower(), page=page)).text
            m = re.search(r"Página <b>\d+</b> de <b>(\d+)</b>", html)
            last = int(m.group(1)) if m else 1
            lots += list(self.parse(html, scope))
            page += 1
        for lot in lots:
            try:
                self.enrich(lot, self.get(lot.url).text)
            except Exception as exc:  # noqa: BLE001
                print(f"  mega: detalhe indisponível para {lot.external_id}: {exc}")
        return lots

    @staticmethod
    def enrich(lot: Lot, page: str) -> None:
        """Detalhe público: valor de avaliação, descrição do lote e sinais da matrícula
        (penhora, indisponibilidade...). Guarda só contagens, sem nomes de partes."""
        lines = page_lines(page)
        for i, ln in enumerate(lines):
            if ln.startswith("Valor de Avaliação"):
                vals = [parse_brl(v) for v in re.findall(r"([\d.]+,\d{2})", " ".join(lines[i:i + 3]))]
                if vals and vals[0]:
                    lot.appraisal_value = vals[0]
                break
        idxs = [i for i, ln in enumerate(lines) if ln == "Descrição"]
        if idxs:
            start = next((j for j in range(idxs[0] + 1, min(idxs[0] + 6, len(lines))) if len(lines[j]) > 60), idxs[0] + 1)
            block = []
            for ln in lines[start:start + 80]:
                if ln.lower().startswith(("visitação", "condições de pagamento", "formas de pagamento", "documentos")):
                    break
                block.append(ln)
            full = " ".join(block)
            first = re.split(r"\bConsta\b", full)[0]
            signals = legal_signal_summary(full)
            low = full.lower()
            if lot.imovel:
                if "desocupad" in low:
                    lot.imovel.occupied = False
                elif "ocupad" in low:
                    lot.imovel.occupied = True
            lot.description = strip_names(first[:900]) + (" " + "; ".join(signals) + "." if signals else "")


    def parse(self, page: str, uf: str | None = None) -> Iterable[Lot]:
        for block in split_blocks(page, '<div class="card ')[:]:
            block = block[:6000]
            m = re.search(r'class="card-title" href="(https://www\.megaleiloes\.com\.br/([a-z-]+)/([a-z-]+)/([a-z]{2})/([a-z-]+)/[^"?]+)[^"]*"[^>]*>(.*?)</a>', block, re.S)
            if not m:
                continue
            url, seg, subcat, lot_uf, _, title = m.groups()
            category = CATEGORIES.get(seg)
            if not category:
                continue  # bens de consumo, animais etc. ficam fora do recorte
            title = text(title)
            ext = re.search(r'card-number[^>]*>([A-Z]?\d+)<', block)
            ext_id = ext.group(1) if ext else url.rsplit("-", 1)[-1]
            locality = re.search(r'class="card-locality"[^>]*title="([^"]+)"', block)
            city = locality.group(1).split(",")[0].strip() if locality else None
            kind = re.search(r'card-instance-title"><a[^>]*>([^<]+)<', block)
            kind = kind.group(1).strip() if kind else ""
            judicial = kind.lower().startswith("judicial")
            icon = re.search(r'card-bank">\s*<img src="([^"]+)"', block)

            inst = re.findall(r'instance-date">(.*?)</span>\s*<span class="card-instance-value">([^<]+)<', block, re.S)
            values = [parse_brl(v.replace("R$", "")) for _, v in inst]
            values = [v for v in values if v]
            if not values:
                price = re.search(r'card-price">([^<]+)<', block)
                values = [parse_brl(price.group(1).replace("R$", ""))] if price else []
            if not values:
                continue
            dates = [iso_dates(d) for d, _ in inst]
            idx = values.index(min(values))
            date = dates[idx][0] if idx < len(dates) and dates[idx] else None
            appraisal = values[0] if len(values) > 1 else None
            down = re.search(r'card-down">.*?class="value">(\d+)%', block, re.S)
            if not appraisal and down:
                appraisal = round(values[-1] / (1 - int(down.group(1)) / 100), 2)

            photo = re.search(r'data-bg="([^"]+)"', block)
            imovel = veiculo = None
            if category == "imovel":
                area = re.search(r"([\d.,]+)\s*m²", title)
                imovel = ImovelInfo(property_type=subcat.replace("-", " "), judicial=judicial,
                                    area_privativa_m2=parse_brl(area.group(1)) if area and "," in area.group(1)
                                    else float(area.group(1).replace(".", "")) if area else None)
            else:
                year = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", title)
                veiculo = VeiculoInfo(model=title.split(" - ")[0], year=int(year.group(1)) if year else None,
                                      vehicle_type=classify(title, subcat))

            yield Lot(
                source_id=self.id, external_id=ext_id, category=category, url=url, title=f"{title.split(' - ')[0]} em {city or lot_uf.upper()}",
                leiloeiro=self.leiloeiro, comitente=_comitente(icon.group(1) if icon else None, judicial),
                sale_type=f"Leilão {kind.lower()}" if kind else "Leilão",
                praca=idx + 1 if len(values) > 1 else None, auction_date=date,
                appraisal_value=appraisal, min_bid=values[idx], city=city, uf=lot_uf.upper(),
                neighborhood=_neighborhood(title, city),
                description=f"{title}. {kind}. " + "; ".join(f"{text(d)} {v.strip()}" for d, v in inst),
                photo_url=photo.group(1) if photo else None, imovel=imovel, veiculo=veiculo,
            )
