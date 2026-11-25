"""Relatório para clientes: e-mail + PDF com o mesmo conteúdo e a mesma identidade visual.

Parâmetro geográfico (radar/geo.py): todo o ES; em MG e BA, até 300 km de São Mateus.

- E-mail: HTML com tabelas e estilos inline. As fotos vão como anexos inline (cid:attN),
  porque Gmail e Outlook bloqueiam imagens externas.
- PDF: HTML de impressão A4 renderizado pelo Chromium (Playwright via Node),
  com fontes embutidas (Playfair Display + Inter) e fotos em data URI.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
import shutil
import sqlite3
import subprocess
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import requests

from . import geo
from .digest import DISCLAIMER, brl, env
from .queries import BASE as LOT_QUERY, _decorate
from .sources.base import USER_AGENT
from .vehicles import LABEL as VEHICLE_LABEL

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]
MES_CURTO = ["", "JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
UF_NAMES = {"ES": "Espírito Santo", "MG": "Minas Gerais", "BA": "Bahia"}
SOURCE_LABEL = {"caixa": "Caixa", "zuk": "Portal Zuk", "mega": "Mega Leilões", "detran_ba": "Detran-BA",
                "esleiloes": "ES Leilões"}
QUOTAS = {"ES": 4, "MG": 2, "BA": 2}  # destaques do PDF; o e-mail mostra 4 deles
UF_COLORS = {"ES": "#d4af6a", "MG": "#7fb3d5", "BA": "#e07a5f"}

AREA = "l.status='aberto' AND l.in_area=1"


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


def fmt_area(l: dict) -> str:
    a = l.get("area_privativa_m2") or l.get("area_total_m2")
    if not a:
        return ""
    txt = f"{a / 10000:.1f} ha".replace(".", ",") if a >= 10000 else f"{a:,.0f} m²".replace(",", ".")
    return txt + ("" if l.get("area_privativa_m2") else " de terreno")


def _lots(conn: sqlite3.Connection, where: str, args: list, order: str, limit: int) -> list[dict]:
    sql = f"{LOT_QUERY} WHERE {AREA} AND {where} ORDER BY {order} LIMIT ?"
    return [_decorate(dict(r)) for r in conn.execute(sql, [*args, limit])]


def _score(l: dict) -> float:
    s = l["discount_pct"] or 0
    if l.get("occupied") == 0:
        s += 8
    elif l.get("occupied") == 1:
        s -= 6
    if l.get("risk_level") == "baixo":
        s += 5
    if l.get("auction_date"):
        s += 2  # leilão com data marcada gera urgência
    return s


def _twin_key(l: dict) -> tuple:
    return (l["title"], l.get("neighborhood"), l["min_bid"])


def pick_highlights(conn: sqlite3.Connection, quotas: dict[str, int] = QUOTAS) -> list[dict]:
    today = date.today().isoformat()
    picks: list[dict] = []
    for uf, n in quotas.items():
        cands = _lots(
            conn,
            "l.uf=? AND l.category='imovel' AND l.lot_count IS NULL AND l.discount_pct >= 30 "
            "AND l.appraisal_value BETWEEN 50000 AND 4000000 AND l.photo_url IS NOT NULL "
            "AND COALESCE(r.level,'') <> 'alto' AND (l.auction_date IS NULL OR l.auction_date >= ?)",
            [uf, today], "l.discount_pct DESC", 80,
        )
        cands.sort(key=_score, reverse=True)
        chosen, cities, twins = [], {}, set()
        for max_per_city in (1, 2):  # variedade: primeiro uma cidade por destaque
            for c in cands:
                if len(chosen) == n:
                    break
                city = (c["city"] or "").lower()
                if c in chosen or _twin_key(c) in twins or cities.get(city, 0) >= max_per_city:
                    continue
                chosen.append(c)
                twins.add(_twin_key(c))
                cities[city] = cities.get(city, 0) + 1
        picks += chosen
    return picks


def ranking(conn: sqlite3.Connection, n: int = 15, exclude: set[int] | None = None) -> list[dict]:
    today = date.today().isoformat()
    rows = _lots(conn, "l.category='imovel' AND l.lot_count IS NULL AND l.appraisal_value >= 50000 "
                       "AND l.discount_pct IS NOT NULL AND (l.auction_date IS NULL OR l.auction_date >= ?)",
                 [today], "l.discount_pct DESC", 6 * n)
    seen, out = set(), []
    for r in rows:
        if r["id"] in (exclude or set()) or _twin_key(r) in seen:
            continue
        seen.add(_twin_key(r))
        out.append(r)
    return out[:n]


def city_summary(conn: sqlite3.Connection, limit: int = 14) -> list[dict]:
    rows = conn.execute(
        f"""SELECT l.city, l.uf, MIN(l.distance_km) dist, COUNT(*) n,
                   AVG(CASE WHEN l.discount_pct > 0 THEN l.discount_pct END) avg_disc,
                   MAX(l.discount_pct) max_disc, MIN(l.min_bid) min_price
            FROM lots l WHERE {AREA} AND l.lot_count IS NULL AND l.category='imovel'
            GROUP BY lower(l.city), l.uf ORDER BY n DESC, avg_disc DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def kpis(conn: sqlite3.Connection) -> dict:
    area = conn.execute(
        f"""SELECT COUNT(*) total,
                  SUM(CASE WHEN discount_pct >= 40 THEN 1 ELSE 0 END) d40,
                  MAX(CASE WHEN appraisal_value >= 50000 THEN discount_pct END) maxd,
                  AVG(CASE WHEN appraisal_value >= 50000 AND discount_pct > 0 THEN discount_pct END) avgd,
                  SUM(CASE WHEN appraisal_value IS NOT NULL AND min_bid IS NOT NULL
                           THEN appraisal_value - min_bid ELSE 0 END) economia,
                  COUNT(DISTINCT lower(city) || uf) cidades
           FROM lots l WHERE {AREA} AND lot_count IS NULL AND category='imovel'"""
    ).fetchone()
    monitored = conn.execute("SELECT COUNT(*) FROM lots WHERE status='aberto'").fetchone()[0]
    by_uf = {r["uf"]: r["n"] for r in conn.execute(
        f"SELECT uf, COUNT(*) n FROM lots l WHERE {AREA} AND lot_count IS NULL AND category='imovel' GROUP BY uf")}
    by_src = {r["source_id"]: r["n"] for r in conn.execute(
        "SELECT source_id, COUNT(*) n FROM lots WHERE status='aberto' GROUP BY source_id")}
    veic = conn.execute(
        f"SELECT COALESCE(SUM(lot_count),0) FROM lots l WHERE {AREA} AND lot_count IS NOT NULL").fetchone()[0]
    vt = {r["vehicle_type"]: r["n"] for r in conn.execute(
        f"""SELECT v.vehicle_type, COUNT(*) n FROM lots l JOIN lot_veiculo v ON v.lot_id = l.id
            WHERE {AREA} AND l.lot_count IS NULL AND l.category='veiculo' GROUP BY v.vehicle_type""")}
    return {**dict(area), "monitored": monitored, "by_uf": by_uf, "by_src": by_src,
            "veiculos": veic, "fontes": len(by_src), "motos": vt.get("moto", 0),
            "automoveis": vt.get("automovel", 0)}


# --- veículos, preço realizado e novidades -------------------------------------

def vehicles(conn: sqlite3.Connection, vtype: str, n: int = 10, since: str | None = None) -> list[dict]:
    """Automóveis ou motos abertos na área: conservados primeiro, mais novos e mais baratos."""
    where, args = "l.category='veiculo' AND l.lot_count IS NULL AND v.vehicle_type = ?", [vtype]
    if since:
        where += " AND l.first_seen_at >= ? AND " + NOT_BASELINE
        args.append(since)
    return _lots(conn, where, args,
                 "CASE WHEN v.classification='conservado' THEN 0 ELSE 1 END, v.year DESC NULLS LAST, l.min_bid ASC", n)


def realized(conn: sqlite3.Connection, n: int = 8, since: str | None = None) -> list[dict]:
    """Lotes arrematados com lance vencedor divulgado: o 'preço realizado'."""
    where, args = "l.final_price IS NOT NULL", []
    if since:
        where += " AND l.closed_at >= ?"
        args.append(since)
    sql = f"""{LOT_QUERY} WHERE l.in_area=1 AND l.status<>'aberto' AND {where}
              ORDER BY (l.final_price / NULLIF(l.min_bid, 0)) DESC LIMIT ?"""
    rows = [_decorate(dict(r)) for r in conn.execute(sql, [*args, n])]
    for r in rows:
        r["uplift"] = (r["final_price"] / r["min_bid"] - 1) * 100 if r.get("min_bid") else None
    return rows


# Lotes da 1ª captura de cada fonte formam a linha de base: não contam como "novidade".
NOT_BASELINE = """l.first_seen_at > (SELECT MIN(started_at) FROM capture_runs c
                                     WHERE c.source_id = l.source_id AND c.scope = l.uf AND c.ok = 1)"""


def changes(conn: sqlite3.Connection, since: str) -> dict:
    new_props = _lots(conn, "l.category='imovel' AND l.lot_count IS NULL AND l.first_seen_at >= ? AND " + NOT_BASELINE,
                      [since], "l.discount_pct DESC NULLS LAST", 40)
    drops = []
    for r in conn.execute(
        f"""SELECT o.lot_id, o.min_bid new_bid, o.observed_at,
                   (SELECT p.min_bid FROM lot_observations p WHERE p.lot_id = o.lot_id AND p.observed_at < o.observed_at
                    ORDER BY p.observed_at DESC LIMIT 1) old_bid
            FROM lot_observations o JOIN lots l ON l.id = o.lot_id
            WHERE o.event = 'preco_alterado' AND o.observed_at >= ? AND l.in_area = 1 AND l.status = 'aberto'
              AND l.lot_count IS NULL""", (since,)):
        if r["old_bid"] and r["new_bid"] and r["new_bid"] < r["old_bid"]:
            lot = _decorate(dict(conn.execute(f"{LOT_QUERY} WHERE l.id = ?", (r["lot_id"],)).fetchone()))
            lot["old_bid"], lot["drop_pct"] = r["old_bid"], (1 - r["new_bid"] / r["old_bid"]) * 100
            drops.append(lot)
    drops.sort(key=lambda x: -x["drop_pct"])
    closed = [_decorate(dict(r)) for r in conn.execute(
        f"""{LOT_QUERY} WHERE l.in_area = 1 AND l.status <> 'aberto' AND l.closed_at >= ? AND l.lot_count IS NULL
              AND l.closed_at > l.first_seen_at
            ORDER BY l.closed_at DESC LIMIT 30""", (since,))]
    new_veh = {t: vehicles(conn, t, 12, since) for t in ("moto", "automovel")}
    return {"new_props": new_props, "drops": drops[:15], "closed": closed, "new_veh": new_veh,
            "total": len(new_props) + len(drops) + len(closed) + sum(len(v) for v in new_veh.values())}


def vehicle_agenda(conn: sqlite3.Connection) -> list[dict]:
    rows = _lots(conn, "l.lot_count IS NOT NULL", [], "l.auction_date ASC", 8)
    for a in rows:  # quais municípios do edital estão dentro do raio
        towns = [t.strip() for t in (a.get("neighborhood") or "").replace(" e ", ",").split(",") if t.strip()]
        near = [(t, geo.distance_km(t, a["uf"])) for t in towns]
        a["near"] = [t for t, d in sorted(near, key=lambda x: x[1] or 9e9)
                     if d is not None and d <= geo.RAIO_KM]
    return rows


# --- gráfico de radar ---------------------------------------------------------

def radar_svg(conn: sqlite3.Connection, size: int = 520, dark: bool = True) -> str:
    """Cidade-base no centro, anéis de 100/200/300 km e um ponto por cidade com lotes.
    O raio do ponto cresce com o número de lotes; a cor indica a UF."""
    cx = cy = size / 2
    rmax = size / 2 - 34
    scale = rmax / max(geo.RAIO_KM, 300)
    ink, faint, label = ("#f3ede2", "rgba(243,237,226,.16)", "rgba(243,237,226,.55)") if dark \
        else ("#14161a", "rgba(20,22,26,.14)", "rgba(20,22,26,.55)")
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" '
         f'height="{size}" font-family="Inter, Arial, sans-serif">',
         '<defs><radialGradient id="sweep" cx="50%" cy="50%" r="50%">'
         '<stop offset="0%" stop-color="#d4af6a" stop-opacity=".22"/>'
         '<stop offset="100%" stop-color="#d4af6a" stop-opacity="0"/></radialGradient></defs>',
         f'<circle cx="{cx}" cy="{cy}" r="{rmax}" fill="url(#sweep)"/>']
    for km in (100, 200, 300):
        r = km * scale
        dash = ' stroke-dasharray="3 5"' if km != 300 else ""
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{faint}" stroke-width="1"{dash}/>')
        p.append(f'<text x="{cx + 4}" y="{cy - r + 13:.1f}" font-size="10" fill="{label}" '
                 f'letter-spacing="1">{km} KM</text>')
    for ang in range(0, 360, 45):
        a = math.radians(ang)
        p.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + rmax * math.sin(a):.1f}" '
                 f'y2="{cy - rmax * math.cos(a):.1f}" stroke="{faint}" stroke-width="1"/>')
    for txt, ang in (("N", 0), ("L", 90), ("S", 180), ("O", 270)):
        a = math.radians(ang)
        p.append(f'<text x="{cx + (rmax + 16) * math.sin(a):.1f}" y="{cy - (rmax + 16) * math.cos(a) + 4:.1f}" '
                 f'font-size="11" font-weight="600" fill="{label}" text-anchor="middle">{txt}</text>')

    rows = conn.execute(
        f"""SELECT city, uf, COUNT(*) n FROM lots l
            WHERE {AREA} AND lot_count IS NULL AND category='imovel' GROUP BY lower(city), uf""").fetchall()
    pts = []
    for r in rows:
        d, b = geo.distance_km(r["city"], r["uf"]), geo.bearing_deg(r["city"], r["uf"])
        if d is None or b is None:
            continue
        rr = min(d, geo.RAIO_KM + 40) * scale
        a = math.radians(b)
        pts.append((r["n"], cx + rr * math.sin(a), cy - rr * math.cos(a), r["uf"], r["city"]))
    for n, x, y, uf, _city in sorted(pts):
        rad = 3.5 + 2.6 * math.sqrt(n)
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}" fill="{UF_COLORS.get(uf, ink)}" '
                 f'fill-opacity=".8" stroke="{"#0b0c0f" if dark else "#fff"}" stroke-width="1.2"/>')
    placed: list[tuple[float, float]] = []
    for n, x, y, _uf, city in sorted(pts, reverse=True):  # rótulos das cidades com mais lotes, sem sobrepor
        if len(placed) == 8 or any(abs(x - px) < 70 and abs(y - py) < 13 for px, py in placed):
            continue
        placed.append((x, y))
        anchor, dx = ("end", -9) if x > cx + rmax * 0.45 else ("start", 9)
        p.append(f'<text x="{x + dx:.1f}" y="{y + 4:.1f}" font-size="10.5" fill="{ink}" '
                 f'font-weight="500" text-anchor="{anchor}">{city.title()} · {n}</text>')
    p.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="#d4af6a"/>'
             f'<circle cx="{cx}" cy="{cy}" r="12" fill="none" stroke="#d4af6a" stroke-width="1.5"/>'
             f'<text x="{cx}" y="{cy + 28}" font-size="11.5" font-weight="700" fill="{ink}" '
             f'text-anchor="middle" letter-spacing="1.5">{geo.CIDADE_BASE.upper()}</text></svg>')
    return "".join(p)


# --- fotos ----------------------------------------------------------------------

def _hires(url: str) -> list[str]:
    """Tenta primeiro a versão maior da foto, depois a original."""
    alts = []
    if "_320x240." in url:
        alts.append(url.replace("_320x240.", "_640x480."))
    if "imagens.portalzuk.com.br/mini/" in url:
        alts.append(url.replace("/mini/", "/detalhe/"))
    return alts + [url]


def _download(url: str, folder: Path) -> Path | None:
    for candidate in _hires(url):
        try:
            r = requests.get(candidate, timeout=30, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
        except requests.RequestException:
            continue
        if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 3000:
            continue
        path = folder / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".jpg")
        path.write_bytes(r.content)
        _crop_banner(path)
        return path
    return None


def _crop_banner(path: Path, w: int = 1200, h: int = 640) -> None:
    """Recorta no formato do card (o Gmail ignora object-fit). Sem Pillow, mantém a original."""
    try:
        from PIL import Image, ImageEnhance
    except ImportError:
        return
    with Image.open(path) as im:
        im = im.convert("RGB")
        # remove molduras/faixas claras que algumas fontes (Caixa) colocam na foto
        im = _trim_light_edges(im)
        ratio, (iw, ih) = w / h, im.size
        if iw / ih > ratio:
            nw = int(ih * ratio)
            box = ((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih)
        else:
            nh = int(iw / ratio)
            box = (0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh)
        im = im.crop(box).resize((w, h), Image.LANCZOS)
        im = ImageEnhance.Contrast(im).enhance(1.05)
        im.save(path, "JPEG", quality=82, optimize=True)


def _trim_light_edges(im, limit: float = 232.0):
    """Corta colunas/linhas de borda quase brancas (média de brilho acima de `limit`)."""
    gray = im.convert("L")
    w, h = gray.size
    px = gray.load()
    step = max(1, h // 60)
    col = lambda x: sum(px[x, y] for y in range(0, h, step)) / len(range(0, h, step))  # noqa: E731
    stepx = max(1, w // 60)
    row = lambda y: sum(px[x, y] for x in range(0, w, stepx)) / len(range(0, w, stepx))  # noqa: E731
    left, right, top, bottom = 0, w - 1, 0, h - 1
    while left < w * 0.3 and col(left) > limit:
        left += 1
    while right > w * 0.7 and col(right) > limit:
        right -= 1
    while top < h * 0.3 and row(top) > limit:
        top += 1
    while bottom > h * 0.7 and row(bottom) > limit:
        bottom -= 1
    if (left, top, right, bottom) == (0, 0, w - 1, h - 1):
        return im
    m = 2  # leva junto a linha fina da moldura
    return im.crop((min(left + m, w // 3), min(top + m, h // 3), max(right - m, 2 * w // 3), max(bottom - m, 2 * h // 3)))


def _data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def _font_faces() -> str:
    folder = resources.files("radar").joinpath("assets/fonts")
    faces = []
    specs = [("Playfair Display", "playfair-display-latin-400-normal.woff2", 400, "normal"),
             ("Playfair Display", "playfair-display-latin-400-italic.woff2", 400, "italic"),
             ("Playfair Display", "playfair-display-latin-700-normal.woff2", 700, "normal"),
             ("Playfair Display", "playfair-display-latin-900-normal.woff2", 900, "normal")]
    specs += [("Inter", f"inter-latin-{w}-normal.woff2", w, "normal") for w in (300, 400, 500, 600, 700, 800)]
    for fam, file, weight, style in specs:
        data = base64.b64encode(folder.joinpath(file).read_bytes()).decode()
        faces.append(f"@font-face{{font-family:'{fam}';font-style:{style};font-weight:{weight};"
                     f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")
    return "\n".join(faces)


# --- montagem -----------------------------------------------------------------

MODES = {
    "completo": ("Varredura completa", "Panorama completo da sua região"),
    "atualizacao": ("Atualização do dia", "O que mudou desde o último envio"),
    "semanal": ("Resumo da semana", "Tudo o que aconteceu nesta semana"),
}


def build_report(conn: sqlite3.Connection, out_dir: str | Path, pdf: bool | None = None,
                 mode: str = "completo", since: str | None = None) -> dict:
    """mode: 'completo' (segunda), 'atualizacao' (terça a sexta) ou 'semanal' (sábado).
    `since` (ISO UTC) delimita as novidades nos modos atualização e semanal."""
    out = Path(out_dir)
    img_dir = out / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    pdf = (mode != "atualizacao") if pdf is None else pdf
    ch = changes(conn, since) if since and mode != "completo" else None

    if mode == "atualizacao":
        highlights = [h for h in (ch["new_props"] if ch else [])
                      if h.get("photo_url") and (h.get("discount_pct") or 0) >= 20][:4]
    else:
        highlights = pick_highlights(conn)
    seen_hashes: set[str] = set()
    for h in highlights:
        path = _download(h["photo_url"], img_dir) if h.get("photo_url") else None
        digest = hashlib.sha1(path.read_bytes()).hexdigest() if path else None
        if path and digest not in seen_hashes:  # fotos repetidas costumam ser "sem foto"
            seen_hashes.add(digest)
            h["img_path"] = path
        h["area_txt"] = fmt_area(h)
    k = kpis(conn)
    label, tagline = MODES[mode]
    ctx = dict(
        mode=mode, mode_label=label, mode_tagline=tagline, ch=ch,
        highlights=highlights, ranking=ranking(conn, 13, {h["id"] for h in highlights}),
        cities=city_summary(conn), agenda=vehicle_agenda(conn), k=k, uf_names=UF_NAMES,
        motos=vehicles(conn, "moto", 10), autos=vehicles(conn, "automovel", 10),
        realized=realized(conn, 8, since if mode == "semanal" else None),
        src=SOURCE_LABEL, hoje=data_extenso(today), fmt_date=fmt_date, fmt_area=fmt_area, brl=brl,
        disclaimer=DISCLAIMER, contact=os.environ.get("RADAR_CONTACT_EMAIL", ""),
        base=geo.CIDADE_BASE, raio=int(geo.RAIO_KM), ufs_inteiras=geo.UFS_INTEIRAS,
        uf_colors=UF_COLORS, mes_curto=MES_CURTO, edicao=today.strftime("%d.%m.%Y"), vlabel=VEHICLE_LABEL,
    )

    # E-mail: até 4 destaques com foto (cid na ordem dos anexos).
    if mode == "atualizacao":
        email_hl = highlights[:4]
    else:
        email_hl = [h for h in highlights if h["uf"] == "ES"][:2] + [h for h in highlights if h["uf"] != "ES"][:2]
    images: list[Path] = []
    for h in email_hl:
        h["cid"] = None
        if h.get("img_path"):
            h["cid"] = f"att{len(images)}"
            images.append(h["img_path"])
    slug = {"completo": "Panorama", "atualizacao": "Atualizacao", "semanal": "Resumo-semanal"}[mode]
    pdf_name = f"Radar-de-Leiloes_{slug}_{today.strftime('%Y-%m-%d')}.pdf"
    html = env.get_template("report.html").render(**ctx, email_highlights=email_hl, pdf_name=pdf_name if pdf else None)
    preview = html
    for i, img in enumerate(images):
        preview = preview.replace(f"cid:att{i}", f"img/{img.name}")
    (out / "relatorio.html").write_text(html, encoding="utf-8")
    (out / "relatorio_preview.html").write_text(preview, encoding="utf-8")

    pdf_path = None
    if pdf:
        for h in highlights:
            h["img_uri"] = _data_uri(h["img_path"]) if h.get("img_path") else None
        pdf_html = env.get_template("report_pdf.html").render(
            **ctx, fonts=_font_faces(), radar=radar_svg(conn, 520, dark=True))
        src = out / "relatorio_pdf.html"
        src.write_text(pdf_html, encoding="utf-8")
        pdf_path = render_pdf(src, out / pdf_name)

    d = today.strftime("%d/%m")
    if mode == "atualizacao":
        n = ch["total"] if ch else 0
        nv = sum(len(v) for v in ch["new_veh"].values()) if ch else 0
        subject = (f"Radar de Leilões · Atualização {d}: {len(ch['new_props'])} imóveis novos, {nv} veículos novos, "
                   f"{len(ch['drops'])} quedas de preço" if n else f"Radar de Leilões · Atualização {d}: sem novidades")
    elif mode == "semanal":
        subject = (f"Radar de Leilões · Resumo da semana ({d}): "
                   f"{len(ch['new_props']) if ch else 0} imóveis novos e {k['d40']} com 40%+ de desconto")
    else:
        subject = (f"Radar de Leilões · Varredura completa {d}: {k['d40']} imóveis com 40%+ de desconto, "
                   f"{k['motos']} motos e {k['automoveis']} carros na sua região")
    return {"html": html, "images": images, "subject": subject, "highlights": highlights,
            "kpis": k, "pdf": pdf_path, "changes": ch, "mode": mode}


def render_pdf(html_path: Path, pdf_path: Path) -> Path | None:
    """Renderiza o PDF com o Chromium do Playwright (Node). Retorna None se indisponível."""
    node = shutil.which("node")
    if not node:
        print("Node.js não encontrado: PDF não gerado.")
        return None
    with resources.as_file(resources.files("radar").joinpath("assets/render_pdf.js")) as js:
        proc = subprocess.run([node, str(js), str(html_path.resolve()), str(pdf_path.resolve())],
                              capture_output=True, text=True, timeout=180, cwd="/tmp")
    if proc.returncode != 0:
        print("Falha ao gerar o PDF:", proc.stderr[-800:])
        return None
    return pdf_path
