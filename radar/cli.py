"""Linha de comando do Radar.

  radar capture [--source caixa] [--uf ES --uf MG] [--file lista.csv]
  radar grade [--limit 20] [--uf ES]        # nota de conservação por IA
  radar digest                              # resumo diário por perfil
  radar health                              # monitor das fontes
  radar review LOTE NIVEL "motivo" --by NOME  # revisão jurídica humana
  radar serve [--port 8000]
  radar daily                               # capture + grade + digest + health (para o cron)
"""
from __future__ import annotations

import argparse
import sys
from contextlib import closing

from .db import connect


def cmd_capture(args) -> int:
    from .pipeline import run_capture
    from .sources import REGISTRY, get_source

    if args.source == "all":
        rc = 0
        for sid in REGISTRY:
            rc |= cmd_capture(argparse.Namespace(**{**vars(args), "source": sid}))
        return rc

    source = get_source(args.source)
    ufs = args.uf or list(source.scopes)
    failures = 0
    with closing(connect(args.db)) as conn:
        for uf in ufs:
            lots = None
            if args.file:
                with open(args.file, encoding=args.encoding) as fh:
                    lots = list(source.parse(fh.read(), uf))  # type: ignore[attr-defined]
            try:
                s = run_capture(conn, source, uf, lots)
                print(f"[{source.id}/{uf}] vistos={s['seen']} novos={s['new']} alterados={s['changed']} "
                      f"encerrados={s['closed']}" + (f"  ⚠ {s['alert']}" if s.get("alert") else ""))
            except Exception as exc:  # noqa: BLE001 — segue para a próxima UF e registra
                failures += 1
                print(f"[{source.id}/{uf}] FALHOU: {exc}", file=sys.stderr)
    return 1 if failures else 0


def cmd_grade(args) -> int:
    from .conservation import grade_pending
    with closing(connect(args.db)) as conn:
        s = grade_pending(conn, limit=args.limit, uf=args.uf)
    print(f"Notas geradas: {s['graded']}  erros: {s['errors']}")
    return 0


def cmd_digest(args) -> int:
    from .digest import run_digests
    with closing(connect(args.db)) as conn:
        for r in run_digests(conn, hours=args.hours):
            print(r)
    return 0


def cmd_report(args) -> int:
    from pathlib import Path

    from .digest import send_email
    from .report import build_report
    out = Path(args.out)
    with closing(connect(args.db)) as conn:
        rep = build_report(conn, out, pdf=not args.no_pdf)
    print(f"Assunto: {rep['subject']}\nDestaques: {len(rep['highlights'])}  fotos no e-mail: {len(rep['images'])}\n"
          f"PDF: {rep['pdf'] or 'não gerado'}\nArquivos em {out}/")
    if args.to:
        ok = send_email(args.to, rep["subject"], rep["html"], rep["images"], [rep["pdf"]] if rep["pdf"] else None)
        print("Enviado." if ok else "SMTP não configurado.")
    return 0


def cmd_rotina(args) -> int:
    from pathlib import Path

    from .digest import send_email
    from .rotina import run
    radars = tuple(args.radar.split(",")) if args.radar != "todos" else ("imoveis", "veiculos", "eletronicos")
    with closing(connect(args.db)) as conn:
        man = run(conn, args.out, mode=None if args.modo == "auto" else args.modo, capture=not args.sem_captura,
                  radars=radars)
    if man and args.smtp:
        for e in man["emails"]:
            html = Path(e["html"]).read_text(encoding="utf-8")
            ok = send_email(e["to"], e["subject"], html, [i["path"] for i in e["inline_images"]],
                            [e["pdf"]["path"]] if e["pdf"] else None)
            print(f"[{e['radar']}] " + ("enviado por SMTP." if ok else "SMTP não configurado: use o manifesto."))
    return 0


def cmd_regiao(args) -> int:
    from . import geo
    from .pipeline import backfill_geo
    with closing(connect(args.db)) as conn:
        n = backfill_geo(conn)
        inside = conn.execute("SELECT COUNT(*) FROM lots WHERE status='aberto' AND in_area=1").fetchone()[0]
    print(f"Base: {geo.CIDADE_BASE}/{geo.UF_BASE} · raio {geo.RAIO_KM:.0f} km · UF inteiras: {', '.join(geo.UFS_INTEIRAS)}")
    print(f"{n} lotes recalculados; {inside} abertos dentro da área.")
    return 0


def cmd_health(args) -> int:
    from .pipeline import health_report
    bad = 0
    with closing(connect(args.db)) as conn:
        for r in health_report(conn):
            flag = "OK " if r["ok"] and not r["alert"] else "!! "
            bad += flag == "!! "
            print(f"{flag}{r['source_id']}/{r['scope']}  {r['finished_at'] or r['started_at']}  "
                  f"vistos={r['lots_seen']}  {r['alert'] or r['error'] or ''}")
    return 1 if bad else 0


def cmd_review(args) -> int:
    from .risk import record_review
    with closing(connect(args.db)) as conn:
        record_review(conn, args.lot_id, args.level, args.reasons, args.by)
    print("Revisão registrada.")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("radar.web:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_daily(args) -> int:
    import os
    from .sources import REGISTRY
    rc = 0
    for sid in REGISTRY:
        rc |= cmd_capture(argparse.Namespace(db=args.db, source=sid, uf=None, file=None, encoding="latin-1"))
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        cmd_grade(argparse.Namespace(db=args.db, limit=args.grade_limit, uf=None))
    else:
        print("Sem credencial da Anthropic: pulando a nota de conservação.")
    cmd_digest(argparse.Namespace(db=args.db, hours=24))
    return cmd_health(args) or rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Radar de Leilões")
    p.add_argument("--db", default=None, help="caminho do SQLite (padrão: data/radar.db ou $RADAR_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="captura lotes de uma fonte")
    c.add_argument("--source", default="caixa", help="caixa | zuk | mega | detran_ba | all")
    c.add_argument("--uf", action="append", help="UF (repetível). Padrão: todas da fonte")
    c.add_argument("--file", help="usar arquivo local em vez de baixar")
    c.add_argument("--encoding", default="latin-1")
    c.set_defaults(func=cmd_capture)

    g = sub.add_parser("grade", help="nota de conservação por IA")
    g.add_argument("--limit", type=int, default=20)
    g.add_argument("--uf")
    g.set_defaults(func=cmd_grade)

    d = sub.add_parser("digest", help="gera/envia o resumo diário")
    d.add_argument("--hours", type=int, default=24)
    d.set_defaults(func=cmd_digest)

    sub.add_parser("health", help="monitor das fontes").set_defaults(func=cmd_health)

    ro = sub.add_parser("rotina", help="rotina diária: seg completa, ter-sex atualização, sáb resumo semanal")
    ro.add_argument("--modo", default="auto", choices=["auto", "completo", "atualizacao", "semanal"])
    ro.add_argument("--out", default="data/relatorio")
    ro.add_argument("--radar", default="todos", help="imoveis | veiculos | eletronicos | todos (ou lista separada por vírgula)")
    ro.add_argument("--smtp", action="store_true", help="enviar cada e-mail por SMTP (SMTP_* e RADAR_EMAIL_*)")
    ro.add_argument("--sem-captura", action="store_true", help="só gera o relatório com os dados atuais")
    ro.set_defaults(func=cmd_rotina)

    sub.add_parser("regiao", help="recalcula distância/área (após mudar cidade-base ou raio)").set_defaults(func=cmd_regiao)

    rp = sub.add_parser("report", help="relatório para clientes (HTML de e-mail)")
    rp.add_argument("--out", default="data/relatorio")
    rp.add_argument("--to", help="enviar por SMTP para este e-mail")
    rp.add_argument("--no-pdf", action="store_true", help="não gerar o PDF")
    rp.set_defaults(func=cmd_report)

    r = sub.add_parser("review", help="registra revisão jurídica humana")
    r.add_argument("lot_id", type=int)
    r.add_argument("level", choices=["baixo", "medio", "alto"])
    r.add_argument("reasons", nargs="+")
    r.add_argument("--by", required=True)
    r.set_defaults(func=cmd_review)

    s = sub.add_parser("serve", help="sobe a interface web")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    dl = sub.add_parser("daily", help="rotina diária completa (cron)")
    dl.add_argument("--grade-limit", type=int, default=50)
    dl.set_defaults(func=cmd_daily)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
