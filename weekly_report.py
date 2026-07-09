#!/usr/bin/env python3
"""
Relatório Semanal TVDE — Faturação + Comportamento de Condução
Corre às segundas-feiras às 12h via cron.
"""

import sqlite3, smtplib, logging, os, sys, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

DB_PATH        = "/opt/tvde/tvde_data.db"
GMAIL_USER     = "adelmotop10@gmail.com"
GMAIL_PASSWORD = "vgkllpncclmxxfyk"
LOG_FILE       = "/opt/tvde/logs/weekly_report.log"

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


import sys as _s; _s.path.insert(0, "/opt/tvde")
from lavagens_email import bloco_lavagens

def get_semana_anterior():
    hoje    = datetime.now().date()
    segunda = hoje - timedelta(days=hoje.weekday())
    return (segunda - timedelta(days=7)).isoformat(), (segunda - timedelta(days=1)).isoformat()


def get_scores_e_metricas(data_ini, data_fim):
    """Query directa à BD — mesma lógica do servidor."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT a.motorista_id,
               SUM(kv.km_total)              AS km,
               SUM(kv.travagens_bruscas)     AS trav,
               SUM(kv.aceleracoes_bruscas)   AS acel,
               SUM(kv.curvas_bruscas)        AS curv,
               MAX(kv.velocidade_max)        AS vmax,
               SUM(kv.excesso_velocidade_eventos) AS speed_events
        FROM km_viaturas kv
        JOIN viaturas    v  ON v.matricula  = kv.matricula
        JOIN atribuicoes a  ON a.viatura_id = v.id AND a.data = kv.data
        WHERE kv.data BETWEEN ? AND ?
        GROUP BY a.motorista_id HAVING km > 10
    """, (data_ini, data_fim)).fetchall()
    conn.close()

    def pen(val, thresh):
        if val <= 0: return 25
        return round(25 * (1 - min(val / thresh, 1.0)))
    def pen_v(v):
        if v <= 120: return 25
        if v <= 130: return 20
        if v <= 140: return 13
        if v <= 160: return 6
        return 0

    scores, metricas = {}, {}
    for r in rows:
        mid  = int(r["motorista_id"])
        km   = max(r["km"] or 1, 1)
        t100 = (r["trav"] or 0) / km * 100
        a100 = (r["acel"] or 0) / km * 100
        c100 = (r["curv"] or 0) / km * 100
        vmax = r["vmax"] or 0
        scores[mid]   = pen(t100,8) + pen(a100,8) + pen(c100,40) + pen_v(vmax)
        metricas[mid] = dict(r)
    return scores, metricas


def get_faturacao_semana(data_ini, data_fim):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT m.id AS motorista_id, m.nome, e.email,
               ROUND(SUM(k.faturacao_liquida),2) AS faturacao_liquida,
               ROUND(SUM(k.gorjetas),2)          AS gorjetas,
               ROUND(SUM(k.bonus),2)             AS bonus,
               SUM(k.num_corridas)               AS total_corridas,
               SUM(k.corridas_uber)              AS corridas_uber,
               SUM(k.corridas_bolt)              AS corridas_bolt,
               ROUND(SUM(k.km_total),1)          AS km_total,
               ROUND(CASE WHEN SUM(k.km_total)>0
                     THEN SUM(k.faturacao_liquida)/SUM(k.km_total)
                     ELSE 0 END, 3)              AS euro_por_km,
               COUNT(DISTINCT k.data)            AS dias_ativos,
               GROUP_CONCAT(DISTINCT k.data)     AS datas_ativas
        FROM motoristas m
        JOIN emails_motoristas e ON e.motorista_id = m.id
        JOIN kpis_diarios      k ON k.motorista_id = m.id
        WHERE m.ativo=1 AND e.activo=1 AND e.email IS NOT NULL AND e.email!=''
          AND k.faturacao_liquida>0 AND k.km_total>0
          AND k.data BETWEEN ? AND ?
        GROUP BY m.id, m.nome, e.email
        ORDER BY euro_por_km DESC
    """, (data_ini, data_fim)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def calcular_ranking(rows):
    n = len(rows)
    for i, r in enumerate(rows):
        r["ranking_pct"] = max(1, round((i/n)*100)) if n>1 else 1
    return rows


DIAS_PT = ["S","T","Q","Q","S","S","D"]

def dias_semana_html(datas_str, data_ini_str):
    ativos = [False]*7
    segunda = datetime.strptime(data_ini_str, "%Y-%m-%d").date()
    for d in (datas_str or "").split(","):
        d = d.strip()
        if not d: continue
        try:
            idx = (datetime.strptime(d,"%Y-%m-%d").date() - segunda).days
            if 0 <= idx < 7: ativos[idx] = True
        except ValueError: pass
    html = ""
    for i,(label,ativo) in enumerate(zip(DIAS_PT,ativos)):
        bg  = "#16a34a" if ativo else "#0f172a"
        cor = "#fff"    if ativo else "#334155"
        brd = "#16a34a" if ativo else "#1e293b"
        sep = "0" if i==6 else "3px"
        html += (f'<td style="padding-right:{sep};">'
                 f'<div style="width:22px;height:22px;border-radius:4px;background:{bg};'
                 f'border:1px solid {brd};text-align:center;line-height:22px;'
                 f'font-size:9px;font-weight:800;color:{cor};">{label}</div></td>')
    return html

def score_cor(s):
    if s>=90: return "#4ade80"
    if s>=75: return "#f0b429"
    if s>=60: return "#f97316"
    return "#ef4444"

def score_label(s):
    if s>=90: return "Excelente"
    if s>=75: return "Bom"
    if s>=60: return "A Melhorar"
    return "Crítico"

def calc_sub_scores(ct):
    km   = max(ct.get("km",1) or 1, 1)
    t100 = (ct.get("trav",0) or 0)/km*100
    a100 = (ct.get("acel",0) or 0)/km*100
    c100 = (ct.get("curv",0) or 0)/km*100
    vmax = ct.get("vmax",0) or 0
    def pen(val,thresh):
        if val<=0: return 25
        return round(25*(1-min(val/thresh,1.0)))
    def pen_v(v):
        if v<=120: return 25
        if v<=130: return 20
        if v<=140: return 13
        if v<=160: return 6
        return 0
    return {"trav":pen(t100,8)*4, "acel":pen(a100,8)*4,
            "curv":pen(c100,40)*4, "vel":pen_v(vmax)*4}

def modo_conducao(score, p33, p66):
    if score>=p66: return ("🌿","ECO",      "#16a34a","#4ade80")
    if score>=p33: return ("🚗","NORMAL",   "#1d4ed8","#60a5fa")
    return               ("⚡","AGRESSIVO","#9f1239","#f87171")

def dica_txt(fat, ct, score, rank):
    trav = ct.get("trav",0) or 0
    acel = ct.get("acel",0) or 0
    vmax = ct.get("vmax",0) or 0
    epkm = fat.get("euro_por_km",0) or 0
    km   = max(ct.get("km",1) or 1,1)
    parts = []
    if rank<=20:
        parts.append(f"Estás no <strong style='color:#f0b429;'>Top {rank}%</strong> em €/km — excelente eficiência 🎉")
    if (trav/km*100)>=6:
        parts.append("Muitas <strong>travagens bruscas</strong> por km — mantém maior distância de segurança.")
    elif (acel/km*100)>=6:
        parts.append("Arranca mais suavemente — reduz consumo e melhora o conforto dos passageiros.")
    if vmax>130:
        parts.append(f"Velocidade máxima de <strong>{vmax} km/h</strong> — mantê-la abaixo de 120 km/h sobe o score.")
    if epkm<0.50:
        parts.append("O €/km está baixo — foca zonas de maior procura e aceita corridas mais longas.")
    if not parts:
        parts.append("Boa semana! Mantém a consistência — é isso que distingue os melhores da frota.")
    return " ".join(parts)

def barra(val):
    pct = min(int(val or 0),100)
    cor = score_cor(val)
    return (f'<div style="background:#1e293b;border-radius:99px;height:6px;overflow:hidden;">'
            f'<div style="width:{pct}%;height:6px;background:{cor};border-radius:99px;"></div></div>')


def build_html(fat, ct, score, data_ini, data_fim, p33, p66):
    nome     = fat["nome"]
    liq      = fat.get("faturacao_liquida",0) or 0
    epkm     = fat.get("euro_por_km",0) or 0
    km       = fat.get("km_total",0) or 0
    dias_at  = fat.get("dias_ativos",0) or 0
    rank     = fat.get("ranking_pct",50)
    gorjetas = fat.get("gorjetas",0) or 0
    bonus    = fat.get("bonus",0) or 0

    trav = ct.get("trav",0) or 0
    acel = ct.get("acel",0) or 0
    curv = ct.get("curv",0) or 0
    vmax = ct.get("vmax",0) or 0

    sub  = calc_sub_scores(ct)
    s_cor= score_cor(score)
    s_lbl= score_label(score)
    em,ml,mc,mct = modo_conducao(score, p33, p66)
    dica = dica_txt(fat, ct, score, rank)

    dias_html  = dias_semana_html(fat.get("datas_ativas",""), data_ini)
    rank_barra = 100 - rank
    liq_int    = f"{int(liq):,}".replace(","," ")
    liq_dec    = f"{liq:.2f}".split(".")[1]
    gorj_line  = ""
    if gorjetas>0 or bonus>0:
        gorj_line = (f'<p style="margin:4px 0 0;color:#64748b;font-size:12px;">'
                     f'+ {gorjetas:.2f}€ gorjetas'
                     +(f' &nbsp;·&nbsp; {bonus:.2f}€ bónus' if bonus>0 else '')+'</p>')
    semana   = (f"{datetime.strptime(data_ini,'%Y-%m-%d').strftime('%d/%m')}"
                f" – {datetime.strptime(data_fim,'%Y-%m-%d').strftime('%d/%m/%Y')}")
    semana_n = datetime.strptime(data_ini,"%Y-%m-%d").strftime("%W")

    return f"""<!DOCTYPE html>
<html lang="pt">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:24px 12px;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;">

<!-- HEADER -->
<tr><td style="background:linear-gradient(135deg,#1e3a5f 0%,#0f172a 100%);
               border-radius:16px 16px 0 0;padding:26px 36px 22px;border-bottom:2px solid #f0b429;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><span style="display:inline-block;background:#f0b42920;border:1px solid #f0b42940;
                     color:#f0b429;font-size:10px;font-weight:800;letter-spacing:3px;
                     text-transform:uppercase;padding:3px 12px;border-radius:99px;">⚡ MOVVI TVDE REPORT</span>
      <p style="margin:8px 0 2px;color:#f1f5f9;font-size:22px;font-weight:800;letter-spacing:-0.5px;">Relatório Semanal</p>
      <p style="margin:0;color:#f1f5f9;font-size:13px;">📅 {semana} &nbsp;·&nbsp; Semana {semana_n}</p>
    </td>
    <td align="right" style="vertical-align:middle;">
      <img src="data:image/png;base64,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" width="100" style="opacity:0.9;filter:brightness(0) invert(1);" alt="Movvi TVDE">
    </td>
  </tr></table>
</td></tr>

<!-- MOTORISTA + MODO -->
<tr><td style="background:#1e293b;padding:18px 36px 16px;border-bottom:1px solid #0f172a;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td><p style="margin:0;color:#f1f5f9;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Motorista</p>
        <p style="margin:4px 0 0;color:#f1f5f9;font-size:19px;font-weight:800;">{nome}</p>
    </td>
    <td align="right">
      <p style="margin:0 0 5px;color:#f1f5f9;font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;text-align:right;">Modo de Condução</p>
      <span style="display:inline-block;background:{mc}22;border:1.5px solid {mc};color:{mct};
                   padding:5px 16px;border-radius:99px;font-size:13px;font-weight:800;">{em} {ml}</span>
    </td>
  </tr></table>
</td></tr>

<!-- FATURAÇÃO TÍTULO -->
<tr><td style="background:#1e293b;padding:18px 36px 6px;">
  <p style="margin:0 0 14px;color:#f1f5f9;font-size:10px;font-weight:800;letter-spacing:3px;text-transform:uppercase;">💶 FATURAÇÃO DA SEMANA</p>
</td></tr>

<!-- Valor líquido + €/km + dias -->
<tr><td style="background:#1e293b;padding:0 36px 14px;">
  <div style="background:linear-gradient(135deg,#052e16 0%,#0f172a 100%);border-radius:12px;padding:20px 24px;border:1px solid #16a34a30;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="44%" style="vertical-align:middle;border-right:1px solid #16a34a20;padding-right:20px;">
        <p style="margin:0 0 2px;color:#4ade8070;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Valor Líquido</p>
        <div><span style="font-size:48px;font-weight:900;color:#4ade80;line-height:1;letter-spacing:-1px;">{liq_int}</span><span style="font-size:26px;font-weight:900;color:#4ade80;">.{liq_dec}</span><span style="font-size:16px;color:#4ade8080;"> €</span></div>
        {gorj_line}
      </td>
      <td style="padding-left:20px;vertical-align:middle;">
        <div style="margin-bottom:14px;">
          <p style="margin:0 0 2px;color:#64748b;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Eficiência</p>
          <span style="font-size:28px;font-weight:900;color:#f1f5f9;">{epkm:.2f}</span>
          <span style="color:#64748b;font-size:13px;"> €/km</span>
        </div>
        <div>
          <p style="margin:0 0 6px;color:#64748b;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Dias Activos</p>
          <table cellpadding="0" cellspacing="0"><tr>{dias_html}</tr></table>
          <p style="margin:4px 0 0;color:#64748b;font-size:11px;"><strong style="color:#f1f5f9;">{dias_at}</strong> de 7 dias</p>
        </div>
      </td>
    </tr></table>
  </div>
</td></tr>

<!-- KM -->
<tr><td style="background:#1e293b;padding:0 36px 14px;">
  <div style="background:#0f172a;border-radius:10px;padding:13px 14px;">
    <p style="margin:0;color:#f1f5f9;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;">🛣️ KM Percorridos</p>
    <p style="margin:4px 0 0;color:#f1f5f9;font-size:26px;font-weight:800;line-height:1;">{km:,.0f} <span style="font-size:14px;color:#64748b;font-weight:400;">km esta semana</span></p>
  </div>
</td></tr>

<!-- Ranking €/km -->
<tr><td style="background:#1e293b;padding:0 36px 20px;">
  <div style="background:#0f172a;border-radius:10px;padding:14px 18px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><p style="margin:0 0 2px;color:#f1f5f9;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">🏆 Ranking €/km na Frota</p>
          <p style="margin:4px 0 0;color:#f1f5f9;font-size:13px;">
            Estás no <strong style="color:#f0b429;font-size:15px;">Top {rank}%</strong>
            <span style="color:#64748b;font-size:12px;"> em eficiência por km</span></p>
      </td>
      <td align="right" style="padding-left:14px;white-space:nowrap;">
        <span style="display:inline-block;background:#f0b42915;border:1.5px solid #f0b42940;color:#f0b429;
                     padding:6px 14px;border-radius:10px;font-size:17px;font-weight:900;">Top {rank}%</span>
      </td>
    </tr></table>
    <div style="margin-top:10px;">
      <div style="background:#1e293b;border-radius:99px;height:7px;overflow:hidden;">
        <div style="width:{rank_barra}%;height:7px;background:linear-gradient(90deg,#16a34a,#f0b429);border-radius:99px;"></div>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:3px;">
        <tr><td style="color:#1e3a5f;font-size:10px;">← Topo</td>
            <td align="right" style="color:#1e3a5f;font-size:10px;">Base →</td></tr>
      </table>
    </div>
  </div>
</td></tr>

<!-- Divisor -->
<tr><td style="background:#1e293b;padding:0 36px 18px;">
  <div style="height:1px;background:linear-gradient(90deg,transparent,#334155,transparent);"></div>
</td></tr>

<!-- CONDUÇÃO TÍTULO -->
<tr><td style="background:#1e293b;padding:0 36px 14px;">
  <p style="margin:0;color:#f1f5f9;font-size:10px;font-weight:800;letter-spacing:3px;text-transform:uppercase;">🚗 COMPORTAMENTO DE CONDUÇÃO</p>
</td></tr>

<!-- Score + barras sub-score -->
<tr><td style="background:#1e293b;padding:0 36px 14px;">
  <div style="background:#0f172a;border-radius:12px;padding:18px 22px;border:1px solid {s_cor}30;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="32%" style="text-align:center;vertical-align:middle;border-right:1px solid #1e293b;padding-right:16px;">
        <p style="margin:0;color:#f1f5f9;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Score</p>
        <div style="font-size:58px;font-weight:900;color:{s_cor};line-height:1;margin:6px 0;">{score}</div>
        <span style="display:inline-block;background:{s_cor}18;color:{s_cor};padding:3px 12px;border-radius:99px;font-size:11px;font-weight:700;">{s_lbl}</span>
      </td>
      <td style="padding-left:20px;vertical-align:middle;">
        <p style="margin:0 0 10px;color:#f1f5f9;font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Score por componente &nbsp;<span style="color:#f1f5f9;font-weight:700;font-style:italic;text-transform:none;letter-spacing:0;">Quanto mais preenchida, melhor.</span></p>
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="padding-bottom:8px;">
            <table width="100%" cellpadding="0" cellspacing="0"><tr>
              <td width="80" style="color:#94a3b8;font-size:11px;">🛑 Travagem</td>
              <td style="padding:0 8px;">{barra(sub['trav'])}</td>
            </tr></table>
          </td></tr>
          <tr><td style="padding-bottom:8px;">
            <table width="100%" cellpadding="0" cellspacing="0"><tr>
              <td width="80" style="color:#94a3b8;font-size:11px;">⚡ Aceleração</td>
              <td style="padding:0 8px;">{barra(sub['acel'])}</td>
            </tr></table>
          </td></tr>
          <tr><td style="padding-bottom:8px;">
            <table width="100%" cellpadding="0" cellspacing="0"><tr>
              <td width="80" style="color:#94a3b8;font-size:11px;">🔄 Curvas</td>
              <td style="padding:0 8px;">{barra(sub['curv'])}</td>
            </tr></table>
          </td></tr>
          <tr><td>
            <table width="100%" cellpadding="0" cellspacing="0"><tr>
              <td width="80" style="color:#94a3b8;font-size:11px;">🏎️ Velocidade</td>
              <td style="padding:0 8px;">{barra(sub['vel'])}</td>
            </tr></table>
          </td></tr>
        </table>
      </td>
    </tr></table>
  </div>
</td></tr>

<!-- 4 cards eventos — totais absolutos da semana -->
<tr><td style="background:#1e293b;padding:0 36px 6px;">
  <p style="margin:0 0 10px;color:#f1f5f9;font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;">Eventos registados — total da semana</p>
</td></tr>
<tr><td style="background:#1e293b;padding:0 36px 20px;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td width="23%" style="padding-right:5px;">
      <div style="background:#0f172a;border-radius:10px;padding:14px 10px;border-top:3px solid #ef4444;text-align:center;">
        <div style="font-size:28px;font-weight:900;color:#ef4444;line-height:1;">{trav}</div>
        <p style="margin:6px 0 0;color:#64748b;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Trav.<br>Bruscas</p>
      </div>
    </td>
    <td width="23%" style="padding:0 5px;">
      <div style="background:#0f172a;border-radius:10px;padding:14px 10px;border-top:3px solid #f0b429;text-align:center;">
        <div style="font-size:28px;font-weight:900;color:#f0b429;line-height:1;">{acel}</div>
        <p style="margin:6px 0 0;color:#64748b;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Acel.<br>Bruscas</p>
      </div>
    </td>
    <td width="23%" style="padding:0 5px;">
      <div style="background:#0f172a;border-radius:10px;padding:14px 10px;border-top:3px solid #818cf8;text-align:center;">
        <div style="font-size:28px;font-weight:900;color:#818cf8;line-height:1;">{curv}</div>
        <p style="margin:6px 0 0;color:#64748b;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Curvas<br>Bruscas</p>
      </div>
    </td>
    <td width="23%" style="padding-left:5px;">
      <div style="background:#0f172a;border-radius:10px;padding:14px 10px;border-top:3px solid #38bdf8;text-align:center;">
        <div style="font-size:28px;font-weight:900;color:#38bdf8;line-height:1;">{vmax:.0f}</div>
        <p style="margin:6px 0 0;color:#64748b;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Vel. Máx.<br>km/h</p>
      </div>
    </td>
  </tr></table>
</td></tr>

<!-- Divisor -->
<tr><td style="background:#1e293b;padding:0 36px 18px;">
  <div style="height:1px;background:linear-gradient(90deg,transparent,#334155,transparent);"></div>
</td></tr>

<!-- DICA -->
<tr><td style="background:#1e293b;padding:0 36px 28px;">
  <div style="background:#0f172a;border-radius:10px;padding:16px 20px;border-left:3px solid #38bdf8;">
    <p style="margin:0 0 5px;color:#38bdf8;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:2px;">💡 Dica da Semana</p>
    <p style="margin:0;color:#cbd5e1;font-size:13px;line-height:1.7;">{dica}</p>
  </div>
</td></tr>

<!-- FOOTER -->
<tr><td style="background:#0f172a;border-radius:0 0 16px 16px;padding:18px 36px;text-align:center;border-top:1px solid #1e293b;">
  <p style="margin:0;color:#334155;font-size:11px;line-height:1.8;">
    Relatório automático · Segundas-feiras às 12h00
  </p>
  <p style="margin:8px 0 0;color:#f1f5f9;font-size:13px;font-weight:700;letter-spacing:0.5px;">
    MOVVI TVDE
  </p>
  <p style="margin:2px 0 0;color:#64748b;font-size:11px;font-style:italic;">
    A frota mais moderna do país.
  </p>
  <p style="margin:8px 0 0;color:#1e3a5f;font-size:10px;line-height:1.6;font-style:italic;">
    Dados preliminares. Sujeitos a confirmação,<br>após conferência no sistema principal.
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def enviar_email(para, nome, html, data_ini):
    semana_n = datetime.strptime(data_ini,"%Y-%m-%d").strftime("%W")
    ano      = datetime.strptime(data_ini,"%Y-%m-%d").strftime("%Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚗 O teu Relatório Semanal — Semana {semana_n}/{ano}"
    msg["From"]    = f"TVDE Fleet Report <{GMAIL_USER}>"
    msg["To"]      = para
    msg.attach(MIMEText(html,"html","utf-8"))
    with smtplib.SMTP("smtp.gmail.com",587) as srv:
        srv.starttls()
        srv.login(GMAIL_USER, GMAIL_PASSWORD)
        srv.sendmail(GMAIL_USER, para, msg.as_string())


def main():
    logging.info("="*60)
    logging.info("INÍCIO RELATÓRIO SEMANAL")
    data_ini, data_fim = get_semana_anterior()
    logging.info(f"Período: {data_ini} → {data_fim}")

    scores, metricas = get_scores_e_metricas(data_ini, data_fim)
    logging.info(f"Cartrack: {len(scores)} motoristas")

    rows = calcular_ranking(get_faturacao_semana(data_ini, data_fim))
    logging.info(f"Faturação: {len(rows)} motoristas")

    if not rows:
        logging.warning("Sem dados de faturação.")
        return

    todos_scores = sorted([v for v in scores.values() if v>0])
    n = len(todos_scores)
    p33 = todos_scores[int(n*0.33)] if n>2 else 50
    p66 = todos_scores[int(n*0.66)] if n>2 else 65
    logging.info(f"Percentis score — P33:{p33} P66:{p66}")

    enviados = erros = sem_ct = 0
    for fat in rows:
        mid   = int(fat["motorista_id"])
        nome  = fat["nome"]
        email = fat["email"]
        score = scores.get(mid,0)
        ct    = metricas.get(mid,{})
        if not ct:
            logging.warning(f"  ⚠ Sem Cartrack: {nome}")
            sem_ct += 1
        try:
            html = build_html(fat, ct, score, data_ini, data_fim, p33, p66)
            html = html.replace("</body>", bloco_lavagens(nome) + "</body>")
            try:
                enviar_email(email, nome, html, data_ini)
            except Exception:
                time.sleep(5)  # pausa e tenta novamente
                enviar_email(email, nome, html, data_ini)
            logging.info(f"  ✓ {nome:30s} → {email}  (€{fat.get('faturacao_liquida',0):.2f} | score {score} | Top {fat.get('ranking_pct',0)}%)")
            enviados += 1
            time.sleep(3)  # pausa entre emails para não saturar o Gmail
        except Exception as e:
            logging.error(f"  ✗ {nome:30s} → ERRO: {e}")
            erros += 1

    logging.info("-"*60)
    logging.info(f"RESULTADO: {enviados} enviados | {sem_ct} sem Cartrack | {erros} erros")
    logging.info("="*60)


if __name__ == "__main__":
    main()
