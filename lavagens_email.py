#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI — Bloco de Lavagens para o Relatório Semanal de Condução
===============================================================
Gera o HTML (compatível com email) do cartão de lavagens de um motorista,
para inserir no weekly_report.py junto ao resumo Cartrack.

Uso no weekly_report.py (2 linhas):
    from lavagens_email import bloco_lavagens
    ...
    html = html.replace("</body>", bloco_lavagens(nome_motorista) + "</body>")
    # ou concatenar onde fizer sentido no template

Teste rápido:
    /opt/tvde/venv/bin/python /opt/tvde/lavagens_email.py "Nome Do Motorista"
"""

import sqlite3
from datetime import datetime, timedelta

DB_PATH = "/opt/tvde/dashboard/lavagens.db"
CICLO   = 5
LINK    = "https://dashboard.movvi.com.pt/lavagens"


def _semana_atual():
    hoje = datetime.now()
    return (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")


def dados_lavagens(nome, db_path=DB_PATH):
    db = sqlite3.connect(db_path)
    try:
        total = db.execute(
            "SELECT COUNT(*) FROM lavagens WHERE motorista=?", (nome,)).fetchone()[0]
        premios = db.execute(
            "SELECT COUNT(*) FROM lavagens_premios WHERE motorista=?", (nome,)).fetchone()[0]
        semana = db.execute(
            "SELECT COUNT(*) FROM lavagens WHERE motorista=? AND date(data) >= ?",
            (nome, _semana_atual())).fetchone()[0]
    except sqlite3.OperationalError:
        return None          # tabelas de lavagens ainda não existem
    finally:
        db.close()
    ciclo = max(0, total - premios * CICLO)
    return {
        "total": total,
        "premios": premios,
        "ciclo": min(ciclo, CICLO),
        "pendente": ciclo >= CICLO,
        "lavou_semana": semana > 0,
    }


def bloco_lavagens(nome, db_path=DB_PATH):
    """Devolve o bloco HTML do cartão de lavagens (string vazia se sem dados)."""
    d = dados_lavagens(nome, db_path)
    if d is None:
        return ""

    # selos (tabela, para compatibilidade com clientes de email)
    selos = ""
    for i in range(CICLO):
        ok = i < d["ciclo"]
        selos += (
            '<td align="center" style="padding:0 4px;">'
            '<div style="width:34px;height:34px;border-radius:50%;line-height:34px;'
            'text-align:center;font-weight:800;font-size:15px;'
            + ("background:#1B7FE4;color:#ffffff;border:2px solid #1B7FE4;"
               if ok else
               "background:#ffffff;color:#93A3B5;border:2px solid #C9D4E0;")
            + f'">{"&#10003;" if ok else i + 1}</div></td>'
        )

    if d["pendente"]:
        estado = ('<div style="background:#EDFAF3;border:1.5px solid #B7E8CE;border-radius:10px;'
                  'padding:10px 14px;margin-top:12px;text-align:center;font-weight:700;color:#14532D;">'
                  '&#127881; Incentivo desbloqueado! Ganhaste 10 fichas de lavagem no Girassol! Fala com a gestGanhaste 10 fichas de lavagem no Girassol! Fala com a gestFala com a gest&atilde;o para receber o teu pr&eacute;mio.atilde;o para levantar.atilde;o para levantar.</div>')
    elif d["lavou_semana"]:
        faltam = CICLO - d["ciclo"]
        estado = ('<div style="background:#EDFAF3;border-radius:10px;padding:8px 14px;margin-top:12px;'
                  'text-align:center;font-weight:600;color:#17825A;font-size:13px;">'
                  f'&#10003; Lavagem desta semana registada &middot; '
                  f'falta{"m" if faltam != 1 else ""} {faltam} para o incentivo</div>')
    else:
        estado = ('<div style="background:#FEF2F2;border-radius:10px;padding:8px 14px;margin-top:12px;'
                  'text-align:center;font-weight:600;color:#B42318;font-size:13px;">'
                  '&#9888; Ainda n&atilde;o registaste a lavagem desta semana &mdash; '
                  f'<a href="{LINK}" style="color:#1B7FE4;">regista aqui com foto</a></div>')

    trofeus = (f' &middot; &#127942; {d["premios"]} incentivo{"s" if d["premios"] != 1 else ""} ganho'
               f'{"s" if d["premios"] != 1 else ""}') if d["premios"] else ""

    return f"""
<!-- ═══ CARTÃO DE LAVAGENS MOVVI ═══ -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin-top:18px;background:#ffffff;border:1px solid #E2E9F1;border-radius:14px;">
  <tr><td style="padding:18px 20px;font-family:Arial,Helvetica,sans-serif;">
    <div style="font-weight:800;font-size:16px;color:#0E1B2C;">&#128663;&#128166; Cart&atilde;o de Lavagens</div>
    <div style="font-size:12px;color:#6B7A8C;margin:2px 0 12px;">
      {d["total"]} lavagen{"s" if d["total"] != 1 else ""} registada{"s" if d["total"] != 1 else ""}{trofeus}
    </div>
    <table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr>{selos}</tr></table>
    {estado}
  </td></tr>
</table>
"""


if __name__ == "__main__":
    import sys
    nome = sys.argv[1] if len(sys.argv) > 1 else ""
    if not nome:
        sys.exit("Uso: lavagens_email.py \"Nome Do Motorista\"")
    d = dados_lavagens(nome)
    print("Dados:", d)
    print("--- HTML ---")
    print(bloco_lavagens(nome)[:400], "...")
