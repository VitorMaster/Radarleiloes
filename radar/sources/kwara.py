"""Kwara (camada 1): plataforma com leiloeiros oficiais (JUCESP) que vende estoques de varejo
e indústria — inclusive "queima de estoque" (produtos novos) — e "logística reversa" (devoluções).

Usado no RADAR DE ELETRÔNICOS. Regra do cliente: só itens NOVOS. Por isso cada lote de
eletrônico tem a página do item lida e passa por `eletronicos.new_evidence`: logística
reversa, "no estado", usado, defeito etc. ficam de fora; sem evidência positiva, também.

Lê apenas páginas públicas (robots.txt permite tudo, exceto *.php).
"""
from __future__ import annotations

import re
from typing import Iterable

from ..eletronicos import kind_of, new_evidence
from ..models import EletronicoInfo, Lot
from ..vehicles import pretty_city
from .base import Source
from .caixa import parse_brl
from .htmlutil import iso_dates, page_lines, text

BASE = "https://www.kwara.com.br"
LISTINGS = ["/categorias/informatica-e-telefonia", "/categorias/eletrodomesticos-e-eletronicos",
            "/oportunidades/queima-de-estoque"]
CARD_LINK = re.compile(r'href="(/bens/[a-z0-9-]+)"')


class KwaraSource(Source):
    may_be_empty = True  # o filtro "só novos" pode, legitimamente, não deixar nada
    id = "kwara"
    name = "Kwara — eletrônicos novos (queima de estoque)"
    layer = 1
    leiloeiro = "Kwara (leiloeiros oficiais JUCESP)"
    verified = True
    scopes = ("BR",)  # eletrônicos: busca nacional (retirada no local indicado em cada lote)
    max_items = 80

    def fetch(self, scope: str) -> Iterable[Lot]:
        cards: dict[str, dict] = {}
        for path in LISTINGS:
            html = self.get(BASE + path).text
            for slug, card in self.parse_cards(html).items():
                cards.setdefault(slug, card)
        lots = []
        for slug, card in list(cards.items())[: self.max_items * 3]:
            if not kind_of(card["title"] + " " + slug.replace("-", " ")):
                continue  # só abre a página de quem parece eletrônico
            if len(lots) >= self.max_items:
                break
            try:
                lot = self.parse_item(self.get(BASE + slug).text, slug, card)
            except Exception as exc:  # noqa: BLE001
                print(f"  kwara: item {slug} ilegível: {exc}")
                continue
            if lot:
                lots.append(lot)
        return lots

    @staticmethod
    def parse_cards(html: str) -> dict[str, dict]:
        """Cards das listagens: título, valor atual, desconto e UF."""
        out = {}
        for m in CARD_LINK.finditer(html):
            slug = m.group(1)
            if slug in out:
                continue
            chunk = html[m.start(): m.start() + 6000]
            title = re.search(r'alt="([^"]+?)(?: - foto \d+)?"', chunk)
            body = text(chunk)
            value = re.search(r"Valor Atual\s*R\$\s*([\d.]+,\d{2})", body)
            off = re.search(r"(\d{1,2})% OFF", body)
            uf = re.search(r"\b([A-Z]{2})\b", body[:400])
            out[slug] = {"title": text(title.group(1)) if title else slug, "value": parse_brl(value.group(1)) if value else None,
                         "off": int(off.group(1)) if off else None, "uf": uf.group(1) if uf else None}
        return out

    def parse_item(self, html: str, slug: str, card: dict) -> Lot | None:
        lines = page_lines(html)
        joined = "\n".join(lines)
        title_m = re.search(r"<title>([^<|]+)", html)
        title = text(title_m.group(1)) if title_m else card["title"]
        kind = kind_of(title)
        if not kind:
            return None
        # texto que descreve o lote e o evento (não o rodapé do site, que cita "Logística Reversa" no menu)
        main = joined.split("Kwara\nBlog")[0]
        event = next((ln for ln in lines if re.search(r"\(K-\d+\)$", ln)), "")
        is_new, proof = new_evidence(title, event, main)
        if not is_new:
            return None  # regra única: só itens novos
        market = re.search(r"Valor de Mercado:\s*\nR\$\s*([\d.]+,\d{2})", joined)
        place = re.search(r"Local de retirada:\s*\n([^\n]+)", joined)
        city = uf = None
        if place:
            pm = re.search(r"([^-–]+)/([A-Z]{2})\s*$", place.group(1))
            if pm:
                city, uf = pretty_city(pm.group(1).strip()), pm.group(2)
        ends = re.search(r"Encerra\s+(\d{2}/\d{2}/\d{2,4})(?:\s*-\s*(\d{1,2})h)?", joined)
        auction_date = None
        if ends:
            d, mth, y = ends.group(1).split("/")
            y = ("20" + y) if len(y) == 2 else y
            auction_date = f"{y}-{mth}-{d}" + (f"T{int(ends.group(2)):02d}:00" if ends.group(2) else "")
        lote = re.search(r"\|\s*Lote\s+(\d+)", html)
        brand = re.search(r"Marca\s*\n:\s*\n([^\n]+)", joined)
        model = re.search(r"Modelo\s*\n:\s*\n([^\n]+)", joined)
        qty = re.search(r"\b(\d{1,4})\s+(?:unidades|un\.|itens|pe[cç]as)\b", title.lower())
        img = re.search(r'src="(https://cdn\.kwara\.com\.br/lot-images/[^"]+)"', html)
        current = card.get("value")
        market_v = parse_brl(market.group(1)) if market else None
        return Lot(
            source_id=self.id, external_id=slug.rsplit("-", 1)[-1], category="eletronico", url=BASE + slug,
            title=title.split(" | ")[0][:140], leiloeiro=self.leiloeiro, comitente=event.split(" (K-")[0][:90] or None,
            sale_type=f"Leilão Kwara{' · lote ' + lote.group(1) if lote else ''}", auction_date=auction_date,
            appraisal_value=market_v, min_bid=current, city=city, uf=uf or card.get("uf"),
            photo_url=(img.group(1).replace(".thumbnail.", ".") if img else None),
            description=f"{title}. Evento: {event or 'n/i'}. Evidência de novo: {proof}. "
                        f"Retirada: {place.group(1) if place else 'n/i'}.",
            eletronico=EletronicoInfo(kind=kind, brand=brand.group(1).strip() if brand else None,
                                      model=model.group(1).strip() if model else None,
                                      quantity=int(qty.group(1)) if qty else None, market_value=market_v,
                                      condition="novo", condition_proof=proof),
        )
