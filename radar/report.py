"""Relatório para clientes: e-mail elegante com destaques, melhores descontos por UF,
agenda de veículos e metodologia.

As fotos vão como anexos inline (cid:attN), porque Gmail e Outlook bloqueiam imagens
externas por padrão. `build_report` devolve o HTML e a lista de imagens na ordem dos cids.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import requests

from .digest import DISCLAIMER, brl, env
from .queries import BASE as LOT_QUERY, _decorate
from .sources.base import USER_AGENT

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]
UF_NAMES = {"ES": "Espírito Santo", "MG": "Minas Gerais", "BA": "Bahia"}
SOURCE_LABEL = {"caixa": "Caixa", "zuk": "Portal Zuk", "mega": "Mega Leilões", "detran_ba": "Detran-BA"}


def data_extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%d/%m") + (f" às {dt.strftime('%Hh%M')}" if "T" in iso else "")


def _open_lots(conn: sqlite3.Connection, where: str, args: list, order: str, limit: int) -> list[dict]:
    sql = f"{LOT_QUERY} WHERE l.status='aberto' AND {where} ORDER BY {order} LIMIT ?"
    return [_decorate(dict(r)) for r in conn.execute(sql, [*args, limit])]


def _score(l: dict) -> float:
    s = l["discount_pct"] or 0
    if l.get("occupied") == 0:
        s += 8
    elif l.get("occupied") == 1:
        s -= 6
    if l.get("risk_level") == "baixo":
        s += 5
    return s


def pick_highlights(conn: sqlite3.Connection, per_uf: int = 2) -> list[dict]:
    today = date.today().isoformat()
    picks: list[dict] = []
    for uf in ("ES", "MG", "BA"):
        cands = _open_lots(
            conn,
            "l.uf=? AND l.category='imovel' AND l.lot_count IS NULL AND l.discount_pct >= 35 "
            "AND l.appraisal_value BETWEEN 80000 AND 3000000 AND l.photo_url IS NOT NULL "
            "AND COALESCE(r.level,'') <> 'alto' AND (l.auction_date IS NULL OR l.auction_date >= ?)",
            [uf, today], "l.discount_pct DESC", 60,
        )
        cands.sort(key=_score, reverse=True)
        chosen, sources = [], set()
        for c in cands:  # diversidade: no máximo um por fonte, depois completa
            if c["source_id"] not in sources:
                chosen.append(c)
                sources.add(c["source_id"])
            if len(chosen) == per_uf:
                break
        for c in cands:
            if len(chosen) == per_uf:
                break
            if c not in chosen:
                chosen.append(c)
        picks += chosen
    return picks


def top_by_uf(conn: sqlite3.Connection, n: int = 5, exclude: set[int] | None = None) -> dict[str, list[dict]]:
    today = date.today().isoformat()
    out = {}
    for uf in ("ES", "MG", "BA"):
        rows = _open_lots(
            conn,
            "l.uf=? AND l.category='imovel' AND l.lot_count IS NULL AND l.appraisal_value >= 60000 "
            "AND l.discount_pct IS NOT NULL AND (l.auction_date IS NULL OR l.auction_date >= ?)",
            [uf, today], "l.discount_pct DESC", 4 * n + len(exclude or ()),
        )
        seen, uniq = set(), []
        for r in rows:  # a Caixa publica lotes gêmeos (mesmo terreno, mesmo preço): mostra um só
            key = (r["title"], r["neighborhood"], r["min_bid"])
            if r["id"] in (exclude or set()) or key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        out[uf] = uniq[:n]
    return out


def kpis(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN discount_pct >= 40 THEN 1 ELSE 0 END) d40,
                  MAX(CASE WHEN appraisal_value >= 60000 THEN discount_pct END) maxd,
                  COUNT(DISTINCT source_id) fontes,
                  SUM(CASE WHEN lot_count IS NOT NULL THEN lot_count ELSE 0 END) veiculos,
                  SUM(CASE WHEN lot_count IS NULL THEN appraisal_value ELSE 0 END) soma_aval
           FROM lots WHERE status='aberto'"""
    ).fetchone()
    by_uf = {r["uf"]: r["n"] for r in conn.execute(
        "SELECT uf, COUNT(*) n FROM lots WHERE status='aberto' AND lot_count IS NULL GROUP BY uf")}
    by_src = {r["source_id"]: r["n"] for r in conn.execute(
        "SELECT source_id, COUNT(*) n FROM lots WHERE status='aberto' GROUP BY source_id")}
    return {**dict(row), "by_uf": by_uf, "by_src": by_src}


def vehicle_agenda(conn: sqlite3.Connection) -> list[dict]:
    return _open_lots(conn, "l.lot_count IS NOT NULL", [], "l.auction_date ASC", 8)


def _hires(url: str) -> list[str]:
    """Tenta primeiro a versão maior da foto (melhor no e-mail), depois a original."""
    alts = []
    if "_320x240." in url:
        alts.append(url.replace("_320x240.", "_640x480."))
    if "imagens.portalzuk.com.br/mini/" in url:
        alts.append(url.replace("/mini/", "/detalhe/"))
    return alts + [url]


def _download(url: str, folder: Path) -> Path | None:
    for candidate in _hires(url):
        path = _download_one(candidate, folder)
        if path:
            return path
    return None


def _download_one(url: str, folder: Path) -> Path | None:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
    except requests.RequestException:
        return None
    if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 3000:
        return None
    path = folder / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".jpg")
    path.write_bytes(r.content)
    _crop_banner(path)
    return path


def _crop_banner(path: Path, w: int = 1200, h: int = 560) -> None:
    """Recorta a foto no formato do card (2,1:1) para não depender de object-fit,
    que o Gmail ignora. Sem Pillow instalado, mantém a foto original."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(path) as im:
        im = im.convert("RGB")
        ratio = w / h
        iw, ih = im.size
        if iw / ih > ratio:
            nw = int(ih * ratio)
            box = ((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih)
        else:
            nh = int(iw / ratio)
            box = (0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh)
        im.crop(box).resize((w, h), Image.LANCZOS).save(path, "JPEG", quality=82, optimize=True)


def build_report(conn: sqlite3.Connection, img_dir: str | Path, cid_offset: int = 0) -> dict:
    img_dir = Path(img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    highlights = pick_highlights(conn)
    images: list[Path] = []
    seen_hashes: set[str] = set()
    for h in highlights:
        p = _download(h["photo_url"], img_dir) if h.get("photo_url") else None
        digest = hashlib.sha1(p.read_bytes()).hexdigest() if p else None
        if p and digest not in seen_hashes:  # fotos repetidas costumam ser "sem foto"
            seen_hashes.add(digest)
            h["cid"] = f"att{cid_offset + len(images)}"
            images.append(p)
    k = kpis(conn)
    today = date.today()
    html = env.get_template("report.html").render(
        highlights=highlights, top=top_by_uf(conn, 5, {h["id"] for h in highlights}),
        agenda=vehicle_agenda(conn), k=k, uf_names=UF_NAMES, src=SOURCE_LABEL,
        hoje=data_extenso(today), fmt_date=fmt_date, contact=os.environ.get("RADAR_CONTACT_EMAIL", ""), brl=brl, disclaimer=DISCLAIMER,
    )
    subject = (f"Radar de Leilões · {k['d40']} imóveis com 40% ou mais de desconto em ES, MG e BA "
               f"— {today.strftime('%d/%m')}")
    return {"html": html, "images": images, "subject": subject, "highlights": highlights, "kpis": k}
