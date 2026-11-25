"""Portal Zuk (camada 1): leilões de imóveis de bancos (Itaú, Santander, Bradesco...),
securitizadoras e processos judiciais. Leiloeiro oficial identificado em cada edital.

Lê apenas as páginas públicas de listagem por UF (permitidas no robots.txt).
O botão "Carregar mais" usa /ajax-lote, que o robots.txt proíbe: por isso
captamos só a primeira página de cada UF e não contornamos essa regra.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..models import ImovelInfo, Lot
from .base import Source
from .caixa import parse_brl
from .htmlutil import iso_dates, legal_signal_summary, page_lines, split_blocks, strip_names, text

LIST_URL = "https://www.portalzuk.com.br/leilao-de-imoveis/u/todos-imoveis/{uf}"
CARD = "card-property card_lotes_div"


class ZukSource(Source):
    id = "zuk"
    name = "Portal Zuk — imóveis de bancos e judiciais"
    layer = 1
    leiloeiro = "Portal Zuk (leiloeiros oficiais por edital)"
    verified = True

    enrich_details = True

    def fetch(self, scope: str) -> Iterable[Lot]:
        lots = list(self.parse(self.get(LIST_URL.format(uf=scope.lower())).text, scope))
        if self.enrich_details:
            for lot in lots:
                try:
                    self.enrich(lot, self.get(lot.url).text)
                except Exception as exc:  # noqa: BLE001 — detalhe é opcional
                    print(f"  zuk: detalhe indisponível para {lot.external_id}: {exc}")
        return lots

    @staticmethod
    def enrich(lot: Lot, page: str) -> None:
        """Página de detalhe (pública): ocupação, descrição, observações e regras de IPTU."""
        lines = page_lines(page)
        joined = "\n".join(lines)
        status = re.search(r"encontra-se (des)?ocupado", joined)
        if status:
            lot.imovel.occupied = not bool(status.group(1))
        desc, obs = "", []
        if "Descrição do imóvel" in lines:
            desc = lines[lines.index("Descrição do imóvel") + 1]
        if "Observações" in lines:
            for ln in lines[lines.index("Observações") + 1:]:
                if ln.lower().startswith(("matricula do im", "matrícula do im", "leia atentamente", "acompanhe",
                                          "código do imóvel", "codigo do imovel", "formas de pagamento")) or len(obs) >= 12:
                    break
                obs.append(ln)
        extra = legal_signal_summary(desc + " " + " ".join(obs))
        if desc or obs:
            lot.description = strip_names((desc + " " + " ".join(obs[:6]))[:1500]) + (" " + "; ".join(extra) if extra else "")

    def parse(self, page: str, uf: str | None = None) -> Iterable[Lot]:
        for block in split_blocks(page, CARD):
            block = block.split("btn_carregarMais")[0][:8000]
            m = re.search(r'href="(https://www\.portalzuk\.com\.br/imovel/[^"]+)"[^>]*title="([^"]*)"', block)
            if not m:
                continue
            url, title_attr = m.group(1), m.group(2)
            ext_id = url.rstrip("/").split("/")[-1]
            parts = [p.strip() for p in title_attr.split(" - ")]
            comitente = parts[-1].split("|")[0].strip() if len(parts) >= 3 else None

            ptype = re.search(r'card-property-price-lote">([^<]+)<', block)
            ptype = ptype.group(1).strip() if ptype else "Imóvel"
            addr = re.search(r'<address class="card-property-address">(.*?)</address>', block, re.S)
            addr_txt = text(addr.group(1)) if addr else ""
            loc = re.match(r"(.+?)\s*/\s*([A-Z]{2})\s*(?:-\s*(.+?))?(?:\s{2,}|$)", addr_txt)
            city = loc.group(1).strip() if loc else None
            lot_uf = loc.group(2) if loc else (uf or "").upper()
            street = [text(s) for s in re.findall(r'margin-left:2\.5rem;">([^<]+)<', block)]
            neighborhood = None
            if loc and loc.group(3):
                neighborhood = (loc.group(3).replace(street[0], "") if street else loc.group(3)).strip() or None

            area = re.search(r"([\d.]+,\d{2})m²\s*([a-zçã]+)?", block)
            info = ImovelInfo(property_type=ptype.lower())
            if area:
                info.area_privativa_m2 = parse_brl(area.group(1))

            pracas = re.findall(
                r'card-property-price-label[^>]*>\s*([^<]+?)\s*</span>\s*<span class="card-property-price-value">\s*R\$\s*([\d.,]+)'
                r'(?:\s*<span class="card-property-price-percent">(.*?)</span>)?.*?card-property-price-data">([^<]*)',
                block, re.S,
            )
            if not pracas:
                continue
            values = [parse_brl(p[1]) for p in pracas]
            current = values[-1]
            pct_txt = text(pracas[-1][2] or "")
            pct = float(re.sub(r"[^\d]", "", pct_txt)) if re.search(r"\d", pct_txt) else None
            if pct and 0 < pct < 100 and current:
                appraisal = round(current / (1 - pct / 100), 2)
            elif len(values) > 1:
                appraisal = values[0]
            else:
                appraisal = None
            dates = iso_dates(pracas[-1][3])
            img = re.search(r'<img src="(https://imagens\.portalzuk\.com\.br/[^"]+)"', block)
            label = pracas[-1][0].lower()
            judicial = "judicial" in title_attr.lower() and "extra" not in title_attr.lower()
            info.judicial = True if judicial else None
            desc = f"{ptype} em {city}/{lot_uf}. {street[0].strip() if street else ''}. Comitente: {comitente or '—'}. " \
                   + "; ".join(f"{p[0].strip()}: R$ {p[1].strip()} em {p[3].strip()}" for p in pracas)
            yield Lot(
                source_id=self.id, external_id=ext_id, category="imovel", url=url,
                title=f"{ptype} em {city or '?'}", leiloeiro=self.leiloeiro, comitente=comitente,
                sale_type=("Leilão — " + label) if "leil" in label else f"Leilão ({label})",
                praca=len(values) if len(values) > 1 else None,
                auction_date=dates[0] if dates else None,
                appraisal_value=appraisal, min_bid=current, city=city, uf=lot_uf,
                neighborhood=neighborhood, address=street[0].strip() if street else None,
                description=desc, photo_url=img.group(1) if img else None, imovel=info,
            )
