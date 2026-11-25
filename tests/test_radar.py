import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar.db import connect
from radar.pipeline import run_capture
from radar.queries import Filters, get_lot, search
from radar.sources.caixa import CaixaSource, parse_brl

FIX = Path(__file__).parent / "fixtures" / "caixa_ES.csv"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def caixa_lots():
    return list(CaixaSource().parse(FIX.read_bytes().decode("latin-1"), "ES"))


def test_parse_brl():
    assert parse_brl("180.000,00") == 180000.0
    assert parse_brl("") is None


def test_caixa_parser():
    lots = caixa_lots()
    assert len(lots) == 3
    a = lots[0]
    assert a.external_id == "8444400012345"
    assert a.city == "Vitoria" and a.uf == "ES"
    assert a.min_bid == 180000 and a.appraisal_value == 300000
    assert a.discount_pct == 40.0
    assert a.imovel.area_privativa_m2 == 72.5 and a.imovel.bedrooms == 2
    assert a.imovel.judicial is False
    assert lots[2].imovel.area_total_m2 == 1200


def test_capture_history_and_closing(conn):
    src = CaixaSource()
    s1 = run_capture(conn, src, "ES", caixa_lots())
    assert s1 == {"seen": 3, "new": 3, "changed": 0, "closed": 0, "alert": None}

    # Dia 2: preço do primeiro caiu e o terceiro sumiu (vendido/retirado)
    lots = caixa_lots()[:2]
    lots[0].min_bid = 150000
    s2 = run_capture(conn, src, "ES", lots)
    assert s2["changed"] == 1 and s2["closed"] == 1
    assert s2["alert"] is None  # queda de 33% fica abaixo do limite de 40%

    first = conn.execute("SELECT id FROM lots WHERE external_id='8444400012345'").fetchone()["id"]
    hist = get_lot(conn, first)["history"]
    assert [h["event"] for h in hist] == ["novo", "preco_alterado"]
    gone = conn.execute("SELECT status FROM lots WHERE external_id='8444400012347'").fetchone()
    assert gone["status"] == "encerrado"


def test_risk_rules(conn):
    run_capture(conn, CaixaSource(), "ES", caixa_lots())
    rows = {r["external_id"]: r for r in search(conn, Filters(ufs=["ES"]))}
    assert rows["8444400012345"]["risk_level"] == "medio"      # ocupado
    assert rows["8444400012346"]["risk_level"] == "baixo"      # desocupado, extrajudicial
    terreno = rows["8444400012347"]
    assert terreno["risk_level"] == "sem_info" and terreno["needs_review"] == 1  # valor alto


def test_filters(conn):
    run_capture(conn, CaixaSource(), "ES", caixa_lots())
    assert len(search(conn, Filters(min_discount=40))) == 2
    assert len(search(conn, Filters(max_risk="baixo"))) == 1
    assert len(search(conn, Filters(cities=["São Mateus".replace("ã", "a")]))) == 1


def test_capture_zero_lots_is_failure(conn):
    with pytest.raises(RuntimeError):
        run_capture(conn, CaixaSource(), "ES", [])
    run = conn.execute("SELECT ok, error FROM capture_runs").fetchone()
    assert run["ok"] == 0 and "zero lotes" in run["error"]


def test_grade_with_fake_client(conn):
    from radar import conservation
    run_capture(conn, CaixaSource(), "ES", caixa_lots())

    class FakeMessages:
        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema"
            body = {"grade": "sem_info", "justification": "Só há dados de cadastro.", "evidence": []}
            return SimpleNamespace(stop_reason="end_turn", model="fake",
                                   content=[SimpleNamespace(type="text", text=json.dumps(body))])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    s = conservation.grade_pending(conn, limit=10, client=fake)
    assert s == {"graded": 3, "errors": 0}
    assert conservation.pending_lots(conn, 10) == []


def test_web(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DB", str(tmp_path / "w.db"))
    import importlib
    import radar.db
    importlib.reload(radar.db)
    import radar.web
    importlib.reload(radar.web)
    from fastapi.testclient import TestClient
    c = radar.db.connect()
    run_capture(c, CaixaSource(), "ES", caixa_lots())
    c.close()
    client = TestClient(radar.web.app)
    r = client.get("/?uf=ES&order=desconto")
    assert r.status_code == 200 and "Jardim Camburi" in r.text
    assert client.get("/lote/1").status_code == 200
    assert client.get("/fontes").status_code == 200
    r = client.post("/perfis", data={"name": "Teste", "category": "imovel", "ufs": ["ES"], "min_discount_pct": "30"})
    assert r.status_code == 200 and "Teste" in r.text
    assert len(client.get("/api/lotes?uf=ES").json()) == 3
