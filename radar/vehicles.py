"""Classificação de veículos: moto, automóvel, caminhão/ônibus ou outro.

As fontes escrevem "MARCA/MODELO" de formas variadas (Detran: "HONDA/CG 125 FAN KS",
Mega: "Fiat Uno 2015", esleiloes: "FIAT STRADA WORKING, ANO: 2014/2015").
"""
from __future__ import annotations

import re
import unicodedata

MOTO_BRANDS = {"YAMAHA", "SUZUKI", "JTA/SUZUKI", "DAFRA", "SHINERAY", "KASINSKI", "TRAXX", "HAOJUE", "SUNDOWN",
               "KAWASAKI", "HARLEY", "HARLEY-DAVIDSON", "TRIUMPH", "DUCATI", "MOTTU", "BULL", "BRAVAX", "AVELLOZ",
               "JONNY", "MVK", "GARINNI", "IROS", "KTM", "ROYAL", "VESPA", "BMW MOTORRAD", "J.TOLEDO", "WUYANG",
               "WUAYANG", "HUARI", "HAOBAO", "SHINERAY", "KYMCO", "LIFAN", "MOTOCAR", "BUENO", "ZONGSHEN"}
MOTO_MODELS = re.compile(
    r"\b(?:CG|CBX|CBR|CB|BIZ|POP|BROS|NXR|XRE|XR|NX|TITAN|TWISTER|FAZER|FACTOR|YBR|LANDER|CROSSER|TENERE|XTZ|"
    r"LEAD|PCX|ELITE|NMAX|XMAX|FLUO|CRYPTON|INTRUDER|BURGMAN|ZIG|RIVA|LIBERTY|MOTONETA|MOTOCICLETA|"
    r"CICLOMOTOR|SCOOTER|TRICICLO|QUADRICICLO|HORNET|TORNADO|APACHE|SPEED|DK|EN)(?=\d|\b)"
    r"|\b\d{2,3} ?CC\b")
TRUCK = re.compile(r"\b(CAMINH|ONIBUS|ÔNIBUS|MICRO-?ONIBUS|ATEGO|AXOR|ACCELO|CONSTELLATION|WORKER|DELIVERY|"
                   r"CARGO|SCANIA|VOLVO/FH|VOLVO/VM|FH ?\d{3}|IVECO|TECTOR|DAILY|REBOQUE|SEMI-?REBOQUE|CARRETA|"
                   r"TRATOR|RETROESCAVADEIRA|PA CARREGADEIRA|MOTONIVELADORA|\d{2}\.\d{3})\b")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()


def classify(text: str, hint: str | None = None) -> str:
    """Retorna 'moto', 'automovel', 'caminhao' ou 'outro'. `hint` = categoria da fonte (ex.: 'motos')."""
    h = _norm(hint or "")
    if "MOTO" in h:
        return "moto"
    if "CAMINH" in h or "ONIBUS" in h or "PESAD" in h:
        return "caminhao"
    if "CARRO" in h or "AUTOMO" in h or "UTILIT" in h:
        return "automovel"
    t = _norm(text)
    brand = t.split("/")[0].strip() if "/" in t[:20] else t.split(" ")[0]
    if TRUCK.search(t):
        return "caminhao"
    words = set(re.split(r"[\s/]+", t))
    if brand in MOTO_BRANDS or words & MOTO_BRANDS or MOTO_MODELS.search(t):
        return "moto"
    if re.search(r"\b(AERONAVE|EMBARCACAO|BARCO|LANCHA|JET ?SKI)\b", t):
        return "outro"
    return "automovel"


LABEL = {"moto": "Motocicleta", "automovel": "Automóvel", "caminhao": "Caminhão/ônibus", "outro": "Outro"}


def pretty_model(raw: str) -> str:
    """'HONDA/CG 125 FAN KS' -> 'Honda CG 125 Fan KS' (mantém siglas curtas em maiúsculas)."""
    raw = re.sub(r"\s+", " ", raw.replace("/", " ")).strip()
    out = []
    for w in raw.split(" "):
        if len(w) <= 3 or any(ch.isdigit() for ch in w):
            keep_case = {"FAN", "POP", "UNO", "KA", "FOX", "BIZ", "YES", "NEO"}
            out.append(w.capitalize() if w.upper() in keep_case else w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def pretty_city(name: str | None) -> str | None:
    """'TEIXEIRA DE FREITAS' -> 'Teixeira de Freitas'."""
    if not name:
        return name
    small = {"de", "da", "do", "das", "dos", "e"}
    words = name.strip().lower().split()
    upper = {"dp", "adm", "fr", "prf", "pm", "pc", "ii", "iii", "iv"}
    return " ".join(w.upper() if w in upper else w if (w in small and i) else w.capitalize()
                    for i, w in enumerate(words))
