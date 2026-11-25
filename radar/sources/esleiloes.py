"""ES Leilões (camada 4, regional ES): leiloeiro oficial capixaba que conduz leilões da
Polícia Civil do ES (viaturas), de prefeituras (São José do Calçado, Muniz Freire...) e de
entidades como a Findes.

Captura veículos (carros, motos, caminhões) e imóveis. Lotes já vendidos entram como
'encerrado' com o lance vencedor: é o "preço realizado" do plano. O nome do arrematante,
que o site exibe, NUNCA é guardado (LGPD).
"""
from __future__ import annotations

import re
from typing import Iterable

from ..models import ImovelInfo, Lot, VeiculoInfo
from ..vehicles import classify, pretty_city, pretty_model
from .base import Source
from .caixa import parse_brl
from .htmlutil import iso_dates, text

BASE = "https://www.esleiloes.com.br"
EVENT_RE = re.compile(r'href="(/eventos/(?:leilao|venda-direta)/(\d+)/[^"]+)"')
VEHICLE_CATS = {"carros": "automovel", "motos": "moto", "caminhoes": "caminhao", "caminhões": "caminhao",
                "utilitarios": "automovel", "utilitários": "automovel", "veiculos": None, "veículos": None}
CLOSED = ("vendido", "encerrado", "cancelado", "retirado", "sem licitante", "não vendido", "nao vendido", "suspenso")


class EsLeiloesSource(Source):
    id = "esleiloes"
    name = "ES Leilões — veículos e bens públicos do ES"
    layer = 4
    leiloeiro = "ES Leilões (leiloeiro oficial)"
    verified = True
    scopes = ("ES",)
    max_pages = 6

    def fetch(self, scope: str) -> Iterable[Lot]:
        home = self.get(BASE + "/").text
        events = dict((eid, path) for path, eid in EVENT_RE.findall(home))
        lots: list[Lot] = []
        for eid, path in events.items():
            page, last = 1, 1
            while page <= min(last, self.max_pages):
                html = self.get(BASE + path + (f"?page={page}" if page > 1 else "")).text
                m = re.search(r"Página\s*(?:<[^>]+>\s*)*\d+\s*(?:<[^>]+>\s*)*de\s*(?:<[^>]+>\s*)*(\d+)", html)
                last = int(m.group(1)) if m else 1
                lots += [l for l in self.parse_event(html, eid, BASE + path) if l.uf in (None, scope)]
                page += 1
        return lots

    def parse_event(self, html: str, event_id: str, event_url: str) -> Iterable[Lot]:
        from .htmlutil import page_lines
        lines = page_lines(html[:200000].split('<article class="lote-main')[0])
        idx = next((i for i, ln in enumerate(lines) if re.fullmatch(r"\d+ lotes", ln)), None)
        event_name = lines[idx + 1] if idx is not None and idx + 1 < len(lines) else f"Leilão {event_id}"
        when = re.search(r"A partir das\s*</strong>\s*<span>([^<]+)</span>", html) or \
            re.search(r"A partir das\s*([\d/]+ [\d:]+)", text(html))
        dates = iso_dates(when.group(1)) if when else []
        for art in html.split('<article class="lote-main')[1:]:
            art = art.split("</article>")[0]
            cat = re.search(r"<h3>([^<]+)</h3>", art)
            desc = re.search(r"<h3>[^<]+</h3>\s*<p>([^<]+)</p>", art)
            if not cat or not desc:
                continue
            cat_name, desc_txt = text(cat.group(1)), text(desc.group(1))
            cat_key = cat_name.lower()
            is_vehicle = cat_key in VEHICLE_CATS or (cat_key == "sucatas" and re.search(r"\bANO\b|/\d{4}", desc_txt))
            is_property = "imó" in cat_key or "imo" in cat_key
            if not (is_vehicle or is_property):
                continue  # bens diversos ficam fora do recorte (imóveis, automóveis e motos)
            cod = re.search(r"COD\.</span>&nbsp;(\d+)", art)
            num = re.search(r"<span>Lote (\d+)</span>", art)
            link = re.search(r'href="(/eventos/[^"]+/lote/\d+/lote)"', art)
            loc = re.search(r'fa-location-dot"></i>\s*<span>([^<]+)</span>', art)
            city, uf = None, None
            if loc:
                parts = [p.strip() for p in text(loc.group(1)).split(" - ")]
                city, uf = pretty_city(parts[0]), (parts[1] if len(parts) > 1 else None)
            initial = re.search(r"Lance Inicial</span>\s*<strong[^>]*>([^<]+)<", art)
            winner = re.search(r"Lance Vencedor</span>\s*<strong[^>]*>([^<]+)<", art)  # o nome vem depois: ignorado
            status_txt = re.search(r'strong-status[^>]*>([^<]+)<', art)
            status_txt = text(status_txt.group(1)).lower() if status_txt else ""
            closed = any(k in status_txt for k in CLOSED)
            img = re.search(r'<img src="([^"]+)"[^>]*class="img-evento"', art)
            year = re.search(r"ANO:?\s*(\d{4})\s*/\s*(\d{4})", desc_txt)
            model = re.split(r",\s*(?:ANO|COR|PLACA|CHASSI)", desc_txt, flags=re.I)[0]
            base = dict(
                source_id=self.id, external_id=cod.group(1) if cod else f"{event_id}-{num.group(1) if num else '?'}",
                url=BASE + link.group(1) if link else event_url, leiloeiro=self.leiloeiro,
                comitente=[p.strip() for p in event_name.split(" - ") if p.strip()][-1][:80].title(),
                sale_type=f"Leilão ES Leilões nº {event_id} · lote {num.group(1) if num else '?'}",
                auction_date=dates[0] if dates else None,
                min_bid=parse_brl((initial.group(1) if initial else "").replace("R$", "")),
                final_price=parse_brl(winner.group(1).replace("R$", "")) if winner and "vendido" in status_txt else None,
                city=city, uf=uf, photo_url=img.group(1) if img else None,
                status="encerrado" if closed else "aberto",
                description=f"{desc_txt}. Categoria: {cat_name}. Situação na fonte: {status_txt or 'n/i'}. "
                            "Vendido no estado em que se encontra.",
            )
            if is_vehicle:
                vtype = VEHICLE_CATS.get(cat_key) or classify(desc_txt)
                label = pretty_model(model)
                yield Lot(category="veiculo", title=f"{label} {year.group(2) if year else ''}".strip(),
                          veiculo=VeiculoInfo(model=label, year=int(year.group(2)) if year else None,
                                              vehicle_type=vtype, origin="bem público inservível",
                                              classification="sucata" if cat_key == "sucatas" else "conservado"),
                          **base)
            else:
                yield Lot(category="imovel", title=f"{model[:60]}", imovel=ImovelInfo(property_type="imóvel"), **base)
