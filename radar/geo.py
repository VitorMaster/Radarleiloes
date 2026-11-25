"""Geografia do radar: distância de cada município até a cidade-base.

Coordenadas dos municípios (IBGE) do conjunto aberto kelvins/municipios-brasileiros (MIT),
recortado para ES, MG e BA em radar/data/municipios_es_mg_ba.csv.
Distância = linha reta (haversine). A distância por estrada costuma ser 20–35% maior.
"""
from __future__ import annotations

import csv
import math
import os
import re
import unicodedata
from functools import lru_cache
from importlib import resources

# Parâmetro do cliente: todo o ES; nos demais estados, até RAIO_KM da cidade-base.
CIDADE_BASE = os.environ.get("RADAR_CIDADE_BASE", "São Mateus")
UF_BASE = os.environ.get("RADAR_UF_BASE", "ES")
RAIO_KM = float(os.environ.get("RADAR_RAIO_KM", "300"))
UFS_INTEIRAS = tuple(u.strip() for u in os.environ.get("RADAR_UFS_INTEIRAS", "ES").split(",") if u.strip())

def norm(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\bd(?:[aeo]s?)\b", " ", s)  # ignora "de/da/do/das/dos" (ex.: "Sao Jose Do Jacuri")
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _table() -> dict[tuple[str, str], tuple[float, float, str]]:
    text = resources.files("radar").joinpath("data/municipios_es_mg_ba.csv").read_text(encoding="utf-8")
    out = {}
    for r in csv.DictReader(text.splitlines()):
        out[(r["uf"], norm(r["nome"]))] = (float(r["lat"]), float(r["lon"]), r["nome"])
    return out


def locate(city: str | None, uf: str | None) -> tuple[float, float, str] | None:
    if not city or not uf:
        return None
    t = _table()
    key = (uf.upper(), norm(city))
    if key in t:
        return t[key]
    # abreviações comuns nas listas ("S Jose Do Calcado", "Sta Maria De Jetiba")
    k = norm(city)
    k = re.sub(r"^s\b", "sao", k)
    k = re.sub(r"^sta\b", "santa", k)
    k = re.sub(r"^sto\b", "santo", k)
    return t.get((uf.upper(), k))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def base() -> tuple[float, float, str]:
    loc = locate(CIDADE_BASE, UF_BASE)
    if not loc:
        raise ValueError(f"Cidade-base não encontrada: {CIDADE_BASE}/{UF_BASE}")
    return loc


def distance_km(city: str | None, uf: str | None) -> float | None:
    loc = locate(city, uf)
    if not loc:
        return None
    b = base()
    return round(haversine_km((b[0], b[1]), (loc[0], loc[1])), 1)


def bearing_deg(city: str | None, uf: str | None) -> float | None:
    """Rumo a partir da cidade-base (0 = norte, 90 = leste): usado no gráfico de radar."""
    loc = locate(city, uf)
    if not loc:
        return None
    b = base()
    lat1, lat2 = math.radians(b[0]), math.radians(loc[0])
    dlon = math.radians(loc[1] - b[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def in_area(uf: str | None, dist: float | None) -> bool:
    """Regra do cliente: UF inteira (ES) ou dentro do raio nos demais estados."""
    if uf in UFS_INTEIRAS:
        return True
    return dist is not None and dist <= RAIO_KM
