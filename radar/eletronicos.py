"""Radar de eletrônicos: classificação do aparelho e a regra única do cliente — SÓ ITENS NOVOS.

Um lote só entra se houver evidência POSITIVA de que é novo (ex.: "novo", "lacrado",
"na caixa", "sem uso", leilão de queima de estoque/estoque de fábrica, mercadoria apreendida
nova) e NENHUM sinal de usado (logística reversa, "no estado", seminovo, defeito, avaria,
sucata, sem garantia de funcionamento...). Na dúvida, fica de fora.
"""
from __future__ import annotations

import re
import unicodedata


def _n(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


KINDS = [  # (tipo, padrão no texto normalizado) — ordem importa
    ("celular", r"\b(celular|smartphone|iphone|galaxy (?:[asmz]\d+|note|z ?flip|z ?fold)|redmi|xiaomi|motorola moto|moto [ge]\d|poco)\b"),
    ("notebook", r"\b(notebook|laptop|macbook|chromebook|ultrabook|thinkpad|elitebook|latitude|vivobook|ideapad)\b"),
    ("tablet", r"\b(tablet|ipad|galaxy tab)\b"),
    ("computador", r"\b(computador|desktop|pc gamer|all[- ]in[- ]one|mini ?pc|imac|mac mini|workstation)\b"),
    ("monitor", r"\b(monitor)\b"),
    ("tv", r"\b(smart ?tv|televisor|televisao|\btv\b|\d{2} ?polegadas)\b"),
    ("console", r"\b(playstation|ps5|ps4|xbox|nintendo|switch oled|console)\b"),
    ("relogio", r"\b(smartwatch|apple watch|galaxy watch|relogio inteligente)\b"),
    ("audio", r"\b(fone|headphone|headset|airpods|caixa de som|soundbar|jbl|home theater)\b"),
    ("camera", r"\b(camera|gopro|drone|dji)\b"),
    ("impressora", r"\b(impressora|multifuncional)\b"),
    ("componente", r"\b(placa de video|placa-mae|placa mae|processador|ssd|memoria ram|gpu|rtx|geforce)\b"),
]

NOT_ELECTRONIC = r"\b(cadeira|mesa|armario|colchao|sofa|talha|inversor|string box|painel solar|bebedouro|poltrona)\b"

NEW_SIGNS = [
    (r"\blacrad[oa]s?\b", "lacrado"),
    (r"\bna caixa\b|\bcaixa original\b|\bembalagem original\b", "na caixa/embalagem original"),
    (r"\bsem uso\b|\bnunca usad", "sem uso"),
    (r"\bnov[oa]s?\b(?! (?:hamburgo|lima|friburgo|iguacu|serrana|venecia|era))", "descrito como novo"),
    (r"\bqueima de estoque\b|\bestoque (?:de fabrica|parado)\b|\bfora de linha\b", "queima de estoque de fábrica/varejo"),
    (r"\bmercadorias? apreendidas?\b.*\bnov", "mercadoria apreendida nova"),
]
USED_SIGNS = [
    r"\blogistica reversa\b", r"\bno estado\b", r"\bseminov", r"\busad[oa]s?\b", r"\brecondicionad",
    r"\brefurbish", r"\bvitrine\b", r"\bmostruario\b", r"\bdefeito", r"\bavaria", r"\bsucata", r"\bdanificad",
    r"\bquebrad", r"\bsem garantia de funcionamento\b", r"\bnao testad", r"\bpara retirada de pecas\b",
    r"\bbloquead", r"\bsem bateria\b", r"\btela trincad", r"\binservive",
]


def kind_of(text: str) -> str | None:
    t = _n(text)
    singular = re.sub(r"\b(\w{4,}?)(?:es|s)\b", r"\1", t)  # "monitores" -> "monitor", "celulares" -> "celular"
    if re.search(NOT_ELECTRONIC, t) or re.search(NOT_ELECTRONIC, singular):
        return None
    for kind, pat in KINDS:
        if re.search(pat, t) or re.search(pat, singular):
            return kind
    return None


def new_evidence(*texts: str) -> tuple[bool, str]:
    """(é_novo, motivo). Exige sinal positivo e nenhum sinal de uso."""
    t = _n(" ".join(x for x in texts if x))
    for pat in USED_SIGNS:
        m = re.search(pat, t)
        if m:
            return False, f"sinal de usado: '{m.group(0)}'"
    for pat, label in NEW_SIGNS:
        if re.search(pat, t):
            return True, label
    return False, "sem evidência de que é novo"


KIND_LABEL = {"celular": "Celular", "notebook": "Notebook", "tablet": "Tablet", "computador": "Computador",
              "monitor": "Monitor", "tv": "TV", "console": "Videogame", "relogio": "Smartwatch", "audio": "Áudio",
              "camera": "Câmera/drone", "impressora": "Impressora", "componente": "Componente de PC"}
