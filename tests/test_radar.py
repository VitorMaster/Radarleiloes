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


# --- novas fontes -----------------------------------------------------------

def _fx(name):
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_caixa_header_and_financing():
    lots = caixa_lots()
    assert lots[0].imovel.accepts_financing is True and lots[1].imovel.accepts_financing is False
    assert lots[0].photo_url.endswith("F844440001234521.jpg")


def test_zuk_parser_and_enrich():
    from radar.risk import evaluate
    from radar.sources.zuk import ZukSource
    lots = list(ZukSource().parse(_fx("zuk_list.html"), "MG"))
    assert len(lots) == 2  # "Aberto para proposta" sem preço é ignorado
    casa, apto = lots
    assert casa.city == "Juiz de Fora" and casa.neighborhood == "Centro" and casa.comitente == "Itaú Unibanco S/A"
    assert casa.min_bid == 160000 and casa.discount_pct == 60.0 and casa.imovel.area_privativa_m2 == 147
    assert apto.uf == "ES" and apto.praca == 2 and apto.min_bid == 600000 and apto.discount_pct == 40.0
    ZukSource.enrich(casa, _fx("zuk_detail.html"))
    assert casa.imovel.occupied is True
    assert "penhora ×1" in casa.description  # a cláusula genérica "Caso haja penhora" não conta
    level, reasons, _ = evaluate(casa)
    assert level == "alto" and "IPTU/débitos anteriores quitados pelo vendedor" in reasons


def test_mega_parser_enrich_and_lgpd():
    from radar.risk import evaluate
    from radar.sources.mega import MegaSource
    lots = list(MegaSource().parse(_fx("mega_list.html"), "BA"))
    assert [l.category for l in lots] == ["imovel", "veiculo"]
    im = lots[0]
    assert im.min_bid == 345132.27 and im.appraisal_value == 690264.54 and im.praca == 2
    assert im.comitente == "Poder Judiciário" and im.sale_type == "Leilão judicial"
    assert lots[1].comitente == "Itaú Unibanco" and lots[1].veiculo.year == 2015
    MegaSource.enrich(im, _fx("mega_detail.html"))
    assert im.appraisal_value == 700000
    assert "Fulano" not in im.description and "Fulana" not in im.description  # LGPD
    assert "penhora ×1" in im.description and "indisponibilidade ×1" in im.description
    assert evaluate(im)[0] == "alto"


def test_detran_ba_detail():
    from radar.sources.detran_ba import DetranBASource
    lot = DetranBASource().parse_detail(_fx("detran_ba_detail.html"), "7765", "https://x")
    assert lot.lot_count == 531 and lot.auction_date == "2099-12-10"
    assert lot.title == "Leilão Detran-BA nº 25/2030 — 531 lotes" and lot.category == "veiculo"
    assert lot.address.endswith("EDITAL.pdf")


def test_report_renders(conn, tmp_path, monkeypatch):
    from radar import report
    monkeypatch.setattr(report, "_download", lambda url, folder: None)
    run_capture(conn, CaixaSource(), "ES", caixa_lots())
    rep = report.build_report(conn, tmp_path, pdf=False)
    assert "Onde o desconto" in rep["html"] and "São Mateus" in rep["html"]
    assert rep["subject"].startswith("Radar de Leilões")
    assert rep["kpis"]["total"] == 3  # todo o ES entra, independentemente da distância
    svg = report.radar_svg(conn)
    assert svg.startswith("<svg") and "SÃO MATEUS" in svg


def test_geo_parameter():
    from radar import geo
    assert geo.distance_km("São Mateus", "ES") == 0
    assert 120 < geo.distance_km("Teixeira de Freitas", "BA") < 140
    assert geo.distance_km("S Jose Do Calcado", "ES") is not None  # abreviação da Caixa
    assert geo.in_area("ES", 900)                                   # ES inteiro
    assert geo.in_area("MG", geo.distance_km("Teófilo Otoni", "MG"))  # ~199 km
    assert not geo.in_area("MG", geo.distance_km("Belo Horizonte", "MG"))  # ~447 km
    assert not geo.in_area("BA", geo.distance_km("Salvador", "BA"))
    assert not geo.in_area("BA", None)


def test_detran_edital_uses_nearest_town(conn):
    from radar.pipeline import _geo
    from radar.models import Lot
    lot = Lot(source_id="detran_ba", external_id="1", category="veiculo", url="u", uf="BA", city="Itabuna",
              neighborhood="Itabuna, Serrinha, Senhor do Bonfim e Teixeira de Freitas", lot_count=106)
    dist, area = _geo(lot)
    assert 120 < dist < 140 and area == 1


# --- veículos e rotina --------------------------------------------------------

def test_vehicle_classification():
    from radar.vehicles import classify, pretty_city
    assert classify("HONDA/CB 300R") == "moto"
    assert classify("I/SHINERAY 50") == "moto"
    assert classify("HUARI MARVA 50CC") == "moto"
    assert classify("YAMAHA/XTZ250 LANDER") == "moto"
    assert classify("FIAT/UNO ATTRACTIVE 1.0") == "automovel"
    assert classify("HYUNDAI/HB20 1.0") == "automovel"
    assert classify("M.BENZ/ATEGO 2425") == "caminhao"
    assert classify("Qualquer coisa", "motos") == "moto"
    assert pretty_city("TEIXEIRA DE FREITAS DP") == "Teixeira de Freitas DP"


ANNEX = """ANEXO I
TEIXEIRA DE FREITAS
LOTE PLACA CHASSI MARCA_MODELO RENAVAN UF COR FAB/MOD COMBUSTIVEL MOTOR
1 OUY8137 9C2JC4110ER106615 HONDA/CG 125 FAN
KS 675270669 BA VERMELHA 2013/2014 GASOLINA JC41E1E106615 SIM CONSERVADO R$ 550,00
2 SUPRIMIDO 9BD***********865 FIAT/UNO ELECTRONIC SUPRIMIDO BA VERDE 1995/1995 GASOLINA 4297251 SIM SUCATA
APROVEITAVEL R$ 550,00
TEIXEIRA DE FREITAS DP
LOTE PLACA CHASSI MARCA_MODELO RENAVAN UF COR FAB/MOD COMBUSTIVEL MOTOR
1 1 EMPLAC 9C2JC30705R000062 HONDA/CG 125 FAN N/I N/I VERMELHA 2005/2005 N/I N/I SIM CONSERVADO R$ 600,00
"""


def test_detran_annex_parser():
    from radar.sources.detran_ba import parse_annex
    rows = parse_annex(ANNEX)
    assert [r["lote"] for r in rows] == [1, 2, 1]
    a, b, c = rows
    assert a["model"] == "HONDA/CG 125 FAN KS" and a["year"] == 2014 and a["value"] == 550
    assert a["city"] == "Teixeira de Freitas" and a["classification"] == "conservado"
    assert b["classification"] == "sucata" and b["model"] == "FIAT/UNO ELECTRONIC"
    assert c["patio"] == "TEIXEIRA DE FREITAS DP" and c["city"] == "Teixeira de Freitas"
    assert all("OUY8137" not in str(r) and "675270669" not in str(r) for r in rows)  # sem placa/Renavam


def test_esleiloes_parser_lgpd(conn):
    from radar.sources.esleiloes import EsLeiloesSource
    lots = list(EsLeiloesSource().parse_event(_fx("esleiloes_evento.html"), "29", "https://x"))
    assert len(lots) == 2  # "bens diversos" fica fora
    sold, moto = lots
    assert sold.status == "encerrado" and sold.final_price == 36500 and sold.min_bid == 13000
    assert sold.city == "Vitória" and sold.veiculo.vehicle_type == "automovel"
    assert "FULANO" not in repr(sold)  # nome do arrematante nunca é guardado
    assert moto.status == "aberto" and moto.veiculo.vehicle_type == "moto" and moto.veiculo.year == 2021
    assert moto.title == "Honda CG 160 Fan 2021"
    assert "Exemplo" in sold.comitente
    run_capture(conn, EsLeiloesSource(), "ES", lots)
    row = conn.execute("SELECT status, final_price FROM lots WHERE external_id='911'").fetchone()
    assert row["status"] == "encerrado" and row["final_price"] == 36500
    ev = conn.execute("SELECT o.event, o.min_bid FROM lot_observations o JOIN lots l ON l.id=o.lot_id "
                      "WHERE l.external_id='911'").fetchone()
    assert ev["event"] == "arrematado" and ev["min_bid"] == 36500


def test_rotina_modes():
    from datetime import datetime
    from radar.rotina import TZ, mode_for
    assert mode_for(datetime(2026, 11, 23, 11, 0, tzinfo=TZ)) == "completo"      # segunda
    assert mode_for(datetime(2026, 11, 25, 11, 0, tzinfo=TZ)) == "atualizacao"   # quarta
    assert mode_for(datetime(2026, 11, 28, 11, 0, tzinfo=TZ)) == "semanal"       # sábado
    assert mode_for(datetime(2026, 11, 29, 11, 0, tzinfo=TZ)) is None            # domingo


def test_update_report_only_news(conn, tmp_path, monkeypatch):
    from radar import report
    monkeypatch.setattr(report, "_download", lambda url, folder: None)
    src = CaixaSource()
    run_capture(conn, src, "ES", caixa_lots())          # linha de base: nada é novidade
    since = conn.execute("SELECT MAX(started_at) FROM capture_runs").fetchone()[0]
    rep = report.build_report(conn, tmp_path / "a", mode="atualizacao", since=since)
    assert rep["changes"]["total"] == 0 and "sem novidades" in rep["subject"]
    import time; time.sleep(1.1)
    lots = caixa_lots()
    lots[0].min_bid = 150000                               # queda de preço
    run_capture(conn, src, "ES", lots)
    rep = report.build_report(conn, tmp_path / "b", mode="atualizacao", since=since)
    assert len(rep["changes"]["drops"]) == 1 and rep["pdf"] is None
    assert round(rep["changes"]["drops"][0]["drop_pct"]) == 17
