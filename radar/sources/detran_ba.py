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
from ..vehicles import classify, pretty_city, pretty_model
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
                if self.vehicles and lot.address:
                    try:
                        lots += self.vehicle_lots(lot, ext_id)
                    except Exception as exc:  # noqa: BLE001 — PDF fora do padrão não derruba a captura
                        print(f"  detran_ba: anexo do edital {ext_id} ilegível: {exc}")
        return lots

    vehicles = True

    def vehicle_lots(self, edital: Lot, ext_id: str) -> list[Lot]:
        import io

        import pypdf
        pdf = pypdf.PdfReader(io.BytesIO(self.get(edital.address).content))
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        out = []
        seen: dict[str, int] = {}
        for r in parse_annex(text):
            key = f"{ext_id}-{(r['patio'] or '').lower().replace(' ', '_')}-{r['lote']}"
            seen[key] = seen.get(key, 0) + 1  # editais repetem seções de pátio: 2ª ocorrência ganha sufixo
            if seen[key] > 1:
                key += f"-{seen[key]}"
            if re.search(r"R\$|\bSIM\b|\b\d{4} \d{4}\b", r["model"]):
                r["model"] = "Veículo (descrição incompleta no edital)"
            vtype = classify(r["model"])
            label = pretty_model(r["model"])
            year = f"{r['year_fab']}/{r['year']}" if r["year"] else "ano n/i"
            cls = "Conservado" if r["classification"] == "conservado" else (r["sucata_detail"] or "Sucata")
            price = f"R$ {r['value']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            patio = pretty_city(r["patio"] or "")
            out.append(Lot(
                source_id=self.id, external_id=key,
                category="veiculo", url=edital.url, title=f"{label} {r['year'] or ''}".strip(),
                leiloeiro=edital.leiloeiro, comitente="Detran-BA",
                sale_type=f"{edital.title.split(' — ')[0]} · lote {r['lote']}", auction_date=edital.auction_date,
                min_bid=r["value"], city=r["city"], uf="BA", neighborhood=f"Pátio {patio}",
                description=(f"{label}, {year}, {r['color'] or 'cor n/i'}, {r['fuel'] or 'combustível n/i'}. "
                             f"{cls}. Lance mínimo {price}. Pátio: {patio}. "
                             "Vendido no estado em que se encontra; visitação conforme edital."),
                veiculo=VeiculoInfo(brand=r["model"].split("/")[0].title() if "/" in r["model"] else None,
                                    model=label, year=r["year"], classification=r["classification"],
                                    origin="apreensão/remoção (Detran)", vehicle_type=vtype,
                                    color=r["color"], fuel=r["fuel"]),
            ))
        return out

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


# --- Anexo I do edital: um registro por veículo ---------------------------------

HEAD_RE = re.compile(r"\n\s*([A-ZÀ-Ú][A-ZÀ-Ú .'-]{2,40}?)\s*\n\s*LOTE PLACA CHASSI")
ROW_START = re.compile(r"(?:^|\n)\s*(\d{1,4}) (?=(?:\d{1,4} )?(?:SUPRIMIDO|EMPLAC\w*|[A-Z]{3}\d[A-Z0-9]\d{2}|\*+|-|N/I) )")
PAGE_HDR = re.compile(r"DEPARTAMENTO ESTADUAL DE TRÂNSITO – EDITAL[^\n]*\n")
SUFFIX = re.compile(r"\s+(DP|ADM|FR|PRF|PM|PC)$")  # "TEIXEIRA DE FREITAS DP" = pátio da delegacia


def parse_annex(text: str) -> list[dict]:
    """Lê a tabela do Anexo I. Não guarda placa, chassi nem Renavam (LGPD/segurança)."""
    start = text.find("ANEXO I")
    if start < 0:
        return []
    t = PAGE_HDR.sub("", text[start:])
    heads = [(m.start(), m.group(1).strip()) for m in HEAD_RE.finditer(t)]
    starts = [(m.start(), m.group(1)) for m in ROW_START.finditer(t)]
    rows = []
    for i, (pos, num) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(t)
        nxt_head = next((hp for hp, _ in heads if pos < hp < end), None)
        chunk = re.sub(r"\s+", " ", t[pos:nxt_head or end]).strip()
        value = re.findall(r"R\$ ?([\d.]+,\d{2})", chunk)
        if not value:
            continue
        toks = chunk.split(" ")[1:]
        if toks and toks[0].isdigit():
            toks = toks[1:]  # coluna extra de numeração em alguns editais
        toks = toks[2:]  # placa e chassi
        model = []
        for tok in toks:
            if tok in ("SUPRIMIDO", "N/I") or re.fullmatch(r"\d{8,11}|\*+\w*", tok):
                break
            model.append(tok)
        rest = toks[len(model) + 1:]
        uf = rest[0] if rest else None
        year = re.search(r"\b(\d{4})/(\d{4})\b", chunk)
        color = None
        if len(rest) > 1:
            color_toks = []
            for tok in rest[1:]:
                if re.fullmatch(r"\d{4}/\d{4}|N/I/N/I|N/I", tok):
                    break
                color_toks.append(tok)
            color = " ".join(color_toks) or None
        fuel = None
        if year:
            after = chunk[year.end():].strip().split(" ")
            fuel = after[0] if after and after[0] not in ("N/I",) else None
        cls = "conservado" if "CONSERVADO" in chunk else "sucata"
        city = None
        for hp, name in heads:
            if hp < pos:
                city = name
        rows.append({
            "lote": int(num), "patio": city, "city": pretty_city(SUFFIX.sub("", city or "")) or None,
            "model": " ".join(model).strip() or "Veículo", "uf": uf if uf and len(uf) == 2 and uf != "N/" else None,
            "color": color.title() if color else None, "fuel": fuel.title() if fuel else None,
            "year_fab": int(year.group(1)) if year else None, "year": int(year.group(2)) if year else None,
            "classification": cls, "value": float(value[-1].replace(".", "").replace(",", ".")),
            "sucata_detail": None if cls == "conservado" else re.sub(
                r".*?(SUCATA.*?)R\$.*", r"\1", chunk).strip().capitalize(),
        })
    return rows
