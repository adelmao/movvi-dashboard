#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI — Gerador de QR codes de lavagens (um por viatura)
=========================================================
Lê as viaturas da BD e gera /opt/tvde/dashboard/qr_lavagens.html:
uma folha pronta a imprimir (A4), com um cartão por viatura.
Cada QR abre a página de lavagens JÁ com essa viatura selecionada.

Requisito (uma vez):
    /opt/tvde/venv/bin/pip install "qrcode[pil]"

Correr:
    /opt/tvde/venv/bin/python /opt/tvde/gerar_qr.py

Ver/imprimir:
    https://dashboard.movvi.com.pt/lavagens/qr?chave=SUA_CHAVE
    (no browser: Ctrl+P → guardar como PDF ou imprimir)
"""

import sqlite3, base64, io
import qrcode

DB_PATH  = "/opt/tvde/tvde_data.db"
SAIDA    = "/opt/tvde/dashboard/qr_lavagens.html"
BASE_URL = "https://dashboard.movvi.com.pt/lavagens"
LOGO     = "https://movvi.com.pt/assets/website/assets/img/logo.png"

db = sqlite3.connect(DB_PATH)
viaturas = db.execute(
    "SELECT DISTINCT matricula, COALESCE(modelo,'') FROM viaturas "
    "WHERE matricula IS NOT NULL ORDER BY matricula").fetchall()
db.close()

def qr_b64(url):
    q = qrcode.QRCode(box_size=7, border=2)
    q.add_data(url)
    q.make(fit=True)
    img = q.make_image(fill_color="#0E1B2C", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

cartoes = []
for matricula, modelo in viaturas:
    url = f"{BASE_URL}?v={matricula}"
    b64 = qr_b64(url)
    cartoes.append(f"""
    <div class="cartao">
      <img class="logo" src="{LOGO}" alt="Movvi">
      <div class="mat">{matricula}</div>
      <div class="mod">{modelo}</div>
      <img class="qr" src="data:image/png;base64,{b64}">
      <div class="txt">Lavou? Aponte a câmara<br>e registe com foto 📷</div>
    </div>""")

html = f"""<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8">
<title>Movvi · QR Lavagens</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 10mm; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6mm; }}
  .cartao {{ border: 1.5px dashed #93A3B5; border-radius: 12px; padding: 6mm 4mm;
            text-align: center; page-break-inside: avoid; }}
  .logo {{ height: 9mm; }}
  .mat {{ font-weight: 800; font-size: 17px; background: #0E1B2C; color: #FFD400;
         display: inline-block; padding: 2px 10px; border-radius: 6px;
         letter-spacing: 2px; margin: 3mm 0 1mm; }}
  .mod {{ font-size: 11px; color: #6B7A8C; margin-bottom: 2mm; }}
  .qr {{ width: 34mm; height: 34mm; }}
  .txt {{ font-size: 10.5px; color: #0E1B2C; font-weight: 600; margin-top: 2mm; }}
  @media print {{ .cabecalho {{ display: none; }} }}
  .cabecalho {{ margin-bottom: 8mm; color: #6B7A8C; font-size: 14px; }}
</style></head><body>
<div class="cabecalho"><b>{len(cartoes)} QR codes gerados.</b>
 Imprima (Ctrl+P), recorte pelo tracejado e cole um em cada viatura
 (porta-luvas ou pala do sol). O QR abre a página de lavagens já com a viatura certa.</div>
<div class="grid">{''.join(cartoes)}</div>
</body></html>"""

open(SAIDA, "w", encoding="utf-8").write(html)
print(f"✓ {len(cartoes)} QR codes gerados em {SAIDA}")
print("  Ver em: https://dashboard.movvi.com.pt/lavagens/qr?chave=SUA_CHAVE")
