"""Detran-BA (camada 2, setor público): editais de leilão de veículos conservados e sucatas.

Cada edital vira um registro "agenda" (lot_count = total de lotes do edital),
com municípios, leiloeiro administrativo, data da disputa e link para o PDF.
O detalhamento lote a lote fica no PDF do edital (próxima etapa: ler o PDF).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable

from ..models import Lot, VeiculoInfo
from .base import Source
from .htmlutil import iso_dates, text

BASE = "https://www.ba.gov.br"
LIST_URL = BASE + "/detran/editais"
LINK_RE = re.compile(r'href="(/detran/editais/(\d+)/edital-de-leilao[^"]*)"[^>]*>([^<]+)</a>')


def _field(page_text: str, label: str) -> str | None:
    m = re.search(r"^" + re.escape(label) + r"\s*:?\s*$\n(.+)", page_text, re.M)
    return m.group(1).strip() if m else None


class DetranBASource(Source):
    id = "detran_ba"
    name = "Detran-BA — leilões de veículos"
    layer = 2
    leiloeiro = "Detran-BA (leiloeiro administrativo)"
    verified = True
    scopes = ("BA",)
    max_age_days = 60

    def fetch(self, scope: str) -> Iterable[Lot]:
        listing = self.get(LIST_URL).text
        seen, lots = set(), []
        for path, ext_id, _title in LINK_RE.findall(listing):
            if ext_id in seen:
                continue
            seen.add(ext_id)
            lot = self.parse_detail(self.get(BASE + path).text, ext_id, BASE + path)
            if lot:
                lots.append(lot)
        return lots

    def parse_detail(self, page: str, ext_id: str, url: str) -> Lot | None:
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
        lines = "\n".join(t for t in (text(x) for x in re.split(r"<[^>]+>", body)) if t)
        title = re.search(r"Edital de Leilão nº\s*[\d/]+[^\n|]*", lines)
        if not title:
            return None
        title = title.group(0).strip()
        published = iso_dates(_field(lines, "Publicado") or "")
        if published and date.fromisoformat(published[0][:10]) < date.today() - timedelta(days=self.max_age_days):
            return None
        session = iso_dates(_field(lines, "Abertura da sessão pública para disputa") or "")
        if session and date.fromisoformat(session[0][:10]) < date.today():
            return None  # leilão já realizado
        towns = _field(lines, "Municípios") or ""
        total = _field(lines, "Total de lotes")
        total_n = int(re.sub(r"\D", "", total)) if total and re.search(r"\d", total) else None
        auctioneer = _field(lines, "Leiloeiro Administrativo")
        site = _field(lines, "Site")
        pdf = re.search(r'href="(/detran/sites/[^"]+\.pdf)"', page)
        number = re.search(r"nº\s*([\d/]+)", title).group(1)
        desc = (f"{title}. Municípios: {towns}. Total de lotes: {total_n or '—'}. "
                f"Leiloeiro administrativo: {auctioneer or '—'}. Site do leilão: {site or '—'}. "
                f"Veículos vendidos no estado em que se encontram; visitação conforme edital.")
        return Lot(
            source_id=self.id, external_id=ext_id, category="veiculo", url=url,
            title=f"Leilão Detran-BA nº {number}" + (f" — {total_n} lotes" if total_n else ""),
            leiloeiro=auctioneer or self.leiloeiro, comitente="Detran-BA",
            sale_type="Leilão administrativo (edital)", auction_date=session[0] if session else None,
            city=towns.split(",")[0].strip() if towns else None, uf="BA", neighborhood=towns or None,
            description=desc, lot_count=total_n,
            photo_url=None, veiculo=VeiculoInfo(origin="apreensão/remoção (Detran)"),
            address=BASE + pdf.group(1) if pdf else None,
        )
