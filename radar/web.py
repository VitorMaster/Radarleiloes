"""Interface web: painel de lotes comparáveis, detalhe com evidências e histórico,
cadastro de perfis de alerta e monitor de fontes."""
from __future__ import annotations

from contextlib import closing

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .db import connect, now_iso
from .digest import DISCLAIMER, brl, env
from .pipeline import health_report
from .queries import Filters, get_lot, search, stats

app = FastAPI(title="Radar de Leilões")
UFS = ["ES", "MG", "BA"]


def render(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(disclaimer=DISCLAIMER, brl=brl, **ctx))


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
def index(request: Request, category: str = "", uf: str = "", city: str = "", min_discount: str = "",
          max_price: str = "", max_risk: str = "", min_grade: str = "", q: str = "",
          order: str = "desconto", page: int = 1):
    f = Filters(
        category=category or None, ufs=[uf] if uf else None, cities=[city] if city else None,
        min_discount=_num(min_discount), max_price=_num(max_price), max_risk=max_risk or None,
        min_grade=min_grade or None, q=q or None, order=order, limit=50, offset=(max(page, 1) - 1) * 50,
    )
    with closing(connect()) as conn:
        lots = search(conn, f)
        st = stats(conn)
    return render("index.html", lots=lots, stats=st, ufs=UFS, args=dict(request.query_params), page=page)


@app.get("/lote/{lot_id}", response_class=HTMLResponse)
def lot_detail(lot_id: int):
    with closing(connect()) as conn:
        lot = get_lot(conn, lot_id)
    if not lot:
        raise HTTPException(404, "Lote não encontrado")
    return render("lot.html", lot=lot)


@app.get("/perfis", response_class=HTMLResponse)
def profiles():
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM profiles ORDER BY id DESC")]
    return render("profiles.html", profiles=rows, ufs=UFS)


@app.post("/perfis")
def create_profile(name: str = Form(...), email: str = Form(""), whatsapp: str = Form(""),
                   category: str = Form("imovel"), ufs: list[str] = Form([]), cities: str = Form(""),
                   min_discount_pct: str = Form(""), max_price: str = Form(""),
                   max_risk: str = Form(""), min_grade: str = Form("")):
    with closing(connect()) as conn:
        conn.execute(
            """INSERT INTO profiles (name, email, whatsapp, category, ufs, cities, min_discount_pct,
               max_price, max_risk, min_grade, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, email or None, whatsapp or None, category or None, ",".join(ufs) or None,
             cities or None, _num(min_discount_pct), _num(max_price), max_risk or None,
             min_grade or None, now_iso()),
        )
        conn.commit()
    return RedirectResponse("/perfis", status_code=303)


@app.get("/fontes", response_class=HTMLResponse)
def sources():
    with closing(connect()) as conn:
        runs = health_report(conn)
        srcs = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY layer, id")]
    return render("sources.html", runs=runs, sources=srcs)


@app.get("/api/lotes")
def api_lots(category: str | None = None, uf: str | None = None, min_discount: float | None = None,
             max_risk: str | None = None, min_grade: str | None = None, limit: int = 100, offset: int = 0):
    """Embrião do feed B2B (despachantes, escritórios). Proteger com chave antes de abrir."""
    with closing(connect()) as conn:
        return search(conn, Filters(category=category, ufs=[uf] if uf else None, min_discount=min_discount,
                                    max_risk=max_risk, min_grade=min_grade, limit=min(limit, 500), offset=offset))
