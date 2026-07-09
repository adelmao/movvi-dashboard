import sys, sqlite3, logging

logging.basicConfig(
    filename='/opt/tvde/prio_alertas/alertas.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

import sys as _sys
_sys.path.insert(0, '/opt/tvde/prio_alertas')
from prio_alertas import init_tabelas_alertas, enviar_whatsapp, get_coordenadas_posto
init_tabelas_alertas()
sys.path.insert(0, '/opt/tvde/prio_alertas')
from prio_alertas import init_tabelas_alertas
init_tabelas_alertas()
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime
sys.path.insert(0, '/opt/tvde/prio_alertas')
from prio_alertas import enviar_whatsapp, get_coordenadas_posto
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_USER     = "adelmotop10@gmail.com"
GMAIL_PASSWORD = "vgkllpncclmxxfyk"

def get_display_name(station):
    try:
        c = sqlite3.connect(DB_ALERTAS)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT display_name FROM prio_postos_geo WHERE station_name=?", (station,)).fetchone()
        c.close()
        if r and r['display_name']:
            parts = r['display_name'].split(',')
            return ', '.join(parts[:3]).strip()
    except:
        pass
    return ''

def enviar_email_motorista(para, nome, data, name, price, kwh, alt_name, alt_price, alt_dist, poupanca, waze):
    try:
        custo_aqui = round(price * kwh, 2)
        custo_alt  = round(alt_price * kwh, 2)
        nome_curto = nome.split()[0]
        rua_alt = get_display_name(alt_name)
        rua_atual = get_display_name(name)
        label_atual = f"{name} ({rua_atual})" if rua_atual else name
        label_alt = f"{alt_name} ({rua_alt})" if rua_alt else alt_name
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
          <div style="background:#1a1a2e;padding:20px;border-radius:8px 8px 0 0;text-align:center">
            <img src="https://dashboard.movvi.com.pt/logomovvi.jpg" alt="Movvi TVDE" style="max-width:160px;margin-bottom:10px">
            <h2 style="color:#00d4aa;margin:0">Alerta Movvi \u2014 Carregamento</h2>
            <p style="color:#aaa;margin:4px 0 0">{data}</p>
          </div>
          <div style="background:#f8f9fa;padding:25px">
            <p>{nome_curto}, no dia <strong>{data}</strong> carregaste em <strong>{label_atual}</strong> a <strong>EUR{price:.3f}/kWh</strong>.</p>
            <br>
            <p>A <strong>{alt_dist}km</strong> existe <strong>{label_alt}</strong> a <strong>EUR{alt_price:.3f}/kWh</strong>.</p>
            <br>
            <table style="width:100%;border-collapse:collapse;font-size:14px">
              <tr style="background:#ffebee">
                <td style="padding:12px;border:1px solid #ddd">{label_atual}</td>
                <td style="padding:12px;border:1px solid #ddd;text-align:right;color:#e74c3c"><strong>EUR{custo_aqui}</strong></td>
              </tr>
              <tr style="background:#e8f5e9">
                <td style="padding:12px;border:1px solid #ddd">{label_alt}</td>
                <td style="padding:12px;border:1px solid #ddd;text-align:right;color:#27ae60"><strong>EUR{custo_alt}</strong></td>
              </tr>
              <tr style="background:#fff3cd">
                <td style="padding:12px;border:1px solid #ddd"><strong>Diferenca</strong></td>
                <td style="padding:12px;border:1px solid #ddd;text-align:right"><strong>EUR{poupanca}</strong></td>
              </tr>
            </table>
            <br>
            <a href="{waze}" style="background:#1a237e;color:#ffffff;padding:14px 28px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;font-size:15px">Ir para o posto mais barato (Waze)</a>
            <br><br>
            <div style="background:#e8f5e9;padding:15px;border-radius:6px;margin-top:15px;border-left:4px solid #00d4aa">
              <p style="font-size:13px;margin:0"><strong>Quer receber este aviso tambem por WhatsApp?</strong><br>
              Envie uma mensagem para <strong>+351224072746</strong> e fique sempre a par das melhores opcoes de carregamento.</p>
            </div>
          </div>
          <div style="background:#f0f0f0;padding:15px;border-radius:0 0 8px 8px;text-align:center">
            <p style="color:#999;font-size:12px;font-style:italic;margin:0">Movvi TVDE \u2014 A melhor parceira do motorista.</p>
            <p style="color:#bbb;font-size:10px;margin:4px 0 0">Sistema criado por <a href="https://adelmo.pt" style="color:#1a237e;text-decoration:none">Adelmo.pt</a></p>
          </div>
        </div>"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Alerta Movvi \u2014 Poupanca potencial EUR{poupanca} ({data})"
        msg["From"]    = f"Movvi TVDE <{GMAIL_USER}>"
        msg["To"]      = para
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587) as srv:
            srv.starttls()
            srv.login(GMAIL_USER, GMAIL_PASSWORD)
            srv.sendmail(GMAIL_USER, para, msg.as_string())
        print(f"OK EMAIL \u2014 {nome} ({para})")
        return True
    except Exception as e:
        print(f"ERRO EMAIL \u2014 {nome} ({para}): {e}")
        return False

DB = '/opt/tvde/tvde_data.db'
DB_ALERTAS = '/opt/tvde/prio_alertas/alertas.db'
JANELA_HORAS = 12  # alertar carregamentos das ultimas N horas

def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def dist(la1,lo1,la2,lo2):
    R=6371; dl=radians(la2-la1); dL=radians(lo2-lo1)
    a=sin(dl/2)**2+cos(radians(la1))*cos(radians(la2))*sin(dL/2)**2
    return R*2*atan2(sqrt(a),sqrt(1-a))

conn = get_conn()

# Mapa preços 14 dias
mapa = {}
for r in conn.execute("""
    SELECT station_name, AVG(valor/kwh) as p
    FROM prio_transacoes
    WHERE kwh>0 AND valor>0 AND data_transacao >= date('now','-14 days')
    GROUP BY station_name
""").fetchall():
    mapa[r['station_name']] = round(r['p'], 4)

# Cache geo
geo = {}
ca = sqlite3.connect(DB_ALERTAS)
ca.row_factory = sqlite3.Row
for r in ca.execute("SELECT station_name, lat, lon FROM prio_postos_geo WHERE lat IS NOT NULL").fetchall():
    geo[r['station_name']] = (float(r['lat']), float(r['lon']))
ca.close()

# Sessoes agrupadas por motorista+posto — evita duplicados
# Um alerta por motorista por posto por dia
sessoes = conn.execute(f"""
    SELECT p.matricula, p.station_name,
           SUM(DISTINCT p.kwh) as kwh, SUM(DISTINCT p.valor) as valor,
           MAX(p.data_transacao) as data_transacao,
           m.nome, m.id as motorista_id,
           e.email, e.telefone
    FROM prio_transacoes p
    JOIN atribuicoes a ON (
        a.viatura_id = (SELECT id FROM viaturas WHERE matricula = p.matricula)
        AND a.data = (
            SELECT MAX(a2.data) FROM atribuicoes a2
            WHERE a2.viatura_id = (SELECT id FROM viaturas WHERE matricula = p.matricula)
              AND a2.data <= date(p.data_transacao)
        )
        AND (
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM atribuicoes a3
                    WHERE a3.viatura_id = a.viatura_id AND a3.data = a.data
                      AND a3.start_hora IS NOT NULL AND a3.start_hora != ''
                )
                THEN a.start_hora IS NOT NULL AND a.start_hora != ''
                     AND p.data_transacao >= a.start_hora
                     AND p.data_transacao <= a.end_hora
                ELSE 1
            END
        )
    )
    JOIN motoristas m ON m.id = a.motorista_id
    JOIN emails_motoristas e ON e.motorista_id = m.id
    WHERE p.kwh > 0 AND p.valor > 0
      AND p.data_transacao >= datetime('now', '-12 hours')
    GROUP BY m.id, p.station_name
    ORDER BY p.matricula
""").fetchall()
conn.close()

print(f"Sessoes unicas hoje (motorista+posto): {len(sessoes)}\n")

alertas = []
vistos = set()  # um alerta por motorista+posto por execucao

# Carregar alertas ja enviados (evitar reenvio)
ja_enviados = set()
# Motoristas em pausa (mesma sugestao 2 dias consecutivos → pausa 7 dias)
motoristas_pausa = set()
try:
    c2 = sqlite3.connect(DB_ALERTAS)
    c2.row_factory = sqlite3.Row
    for r in c2.execute("""
        SELECT data_transacao, matricula FROM prio_alertas_enviados
        WHERE tipo='imediato'
    """).fetchall():
        ja_enviados.add((r['data_transacao'], r['matricula']))
    # Verificar motoristas com mesma sugestao 2 dias consecutivos
    rows_pausa = c2.execute("""
        SELECT matricula, alt_station, COUNT(DISTINCT date(enviado_em)) as dias
        FROM prio_alertas_enviados
        WHERE tipo='imediato'
          AND date(enviado_em) >= date('now','-2 days')
          AND alt_station IS NOT NULL
        GROUP BY matricula, alt_station
        HAVING dias >= 2
    """).fetchall()
    for r in rows_pausa:
        # Verificar se ja tem 7 dias desde ultima pausa
        ultimo = c2.execute("""
            SELECT MAX(enviado_em) FROM prio_alertas_enviados
            WHERE matricula=? AND alt_station=? AND tipo='imediato'
        """, (r['matricula'], r['alt_station'])).fetchone()
        if ultimo:
            motoristas_pausa.add((r['matricula'], r['alt_station']))
    c2.close()
except Exception as e:
    print(f"Erro carregar duplicados: {e}")

for s in sessoes:
    motor_id = s['motorista_id']
    # Só um alerta por motorista por dia — o maior poupança
    name  = s['station_name']
    price = round(s['valor']/s['kwh'], 4) if s['kwh'] > 0 else 0

    # Saltar se ja foi alertado
    if (s['data_transacao'], s['matricula']) in ja_enviados:
        continue
    kwh   = float(s['kwh'])
    motor = s['nome']
    tel   = (s['telefone'] or '').strip()
    email = (s['email'] or '').strip()
    data  = datetime.strptime(s['data_transacao'][:10], '%Y-%m-%d').strftime('%d-%m-%Y')

    if price <= 0:
        continue

    # Geocodificar se necessário
    if name not in geo:
        get_coordenadas_posto(name)
        c = sqlite3.connect(DB_ALERTAS)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT lat, lon FROM prio_postos_geo WHERE station_name=?", (name,)).fetchone()
        c.close()
        if r and r['lat']:
            geo[name] = (float(r['lat']), float(r['lon']))

    if name not in geo:
        continue

    lat, lon = geo[name]
    melhor = None
    for n2, p2 in mapa.items():
        if n2 == name or price - p2 < 0.05: continue
        if n2 not in geo: continue
        d = dist(lat, lon, geo[n2][0], geo[n2][1])
        if d <= 3.0:
            if not melhor or p2 < melhor[1]:
                melhor = (n2, p2, round(d, 2))

    if not melhor:
        continue

    poupanca = round((price - melhor[1]) * kwh, 2)

    # Se este motorista já tem alerta, guardar só se poupança maior
    if motor_id in vistos:
        # Substituir se poupança maior
        for i, a in enumerate(alertas):
            if a['motor_id'] == motor_id and poupanca > a['poupanca']:
                alertas[i] = {**a, 'name': name, 'price': price, 'kwh': kwh,
                               'alt_name': melhor[0], 'alt_price': melhor[1],
                               'alt_dist': melhor[2], 'poupanca': poupanca}
        continue

    vistos.add(motor_id)
    alertas.append({
        'motor_id': motor_id, 'motor': motor, 'mat': s['matricula'],
        'tel': tel, 'email': email, 'name': name, 'price': price,
        'kwh': kwh, 'data': data, 'data_transacao': s['data_transacao'], 'alt_name': melhor[0],
        'alt_price': melhor[1], 'alt_dist': melhor[2], 'poupanca': poupanca,
    })

print(f"{'='*55}")
print(f"SIMULACAO — {len(alertas)} motoristas a alertar:")
print(f"{'='*55}")
for a in sorted(alertas, key=lambda x: x['poupanca'], reverse=True):
    canal = 'WhatsApp' if a['tel'] else 'Email'
    print(f"  {a['motor'][:35]:35} | {canal:8} | poupar €{a['poupanca']:.2f}")
    print(f"    {a['name']} €{a['price']:.3f} → {a['alt_name']} €{a['alt_price']:.3f} a {a['alt_dist']}km")

total_poupanca = sum(a['poupanca'] for a in alertas)
print(f"{'='*55}")
print(f"Poupanca total potencial: €{total_poupanca:.2f}")
print(f"{'='*55}")

ENVIAR = len(sys.argv) > 1 and sys.argv[1] == 'enviar'

if ENVIAR:
    enviados = 0
    for a in alertas:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        c2b = sqlite3.connect(DB_ALERTAS)
        c2b.row_factory = sqlite3.Row
        g = c2b.execute("SELECT lat, lon FROM prio_postos_geo WHERE station_name=?", (a['alt_name'],)).fetchone()
        c.close()
        waze = f"https://waze.com/ul?ll={g['lat']},{g['lon']}&navigate=yes" if g else ""

        nome_curto = a['motor'].split()[0]
        custo_aqui = round(a['price'] * a['kwh'], 2)
        custo_alt  = round(a['alt_price'] * a['kwh'], 2)

        # Buscar nomes de rua
        rua_atual = get_display_name(a['name'])
        rua_alt   = get_display_name(a['alt_name'])
        wa_atual = f"{a['name']} ({rua_atual})" if rua_atual else a['name']
        wa_alt   = f"{a['alt_name']} ({rua_alt})" if rua_alt else a['alt_name']

        msg = (
            f"Alerta Movvi - carregamento\n\n"
            f"{nome_curto}, no dia *{a['data']}* carregaste em *{wa_atual}* a *EUR{a['price']:.3f}/kWh*.\n\n"
            f"A *{a['alt_dist']}km* existe *{wa_alt}* a *EUR{a['alt_price']:.3f}/kWh*.\n\n"
            f"Para os teus {a['kwh']:.0f} kWh:\n"
            f"- {wa_atual}: EUR{custo_aqui}\n"
            f"- {wa_alt}: EUR{custo_alt}\n"
            f"- Diferenca: *EUR{a['poupanca']}*\n\n"
            f"Clique aqui e va para o posto mais barato:\n{waze}\n\n"
            f"_Movvi TVDE - A melhor parceira do motorista._"
        )

        # Email para todos
        if a['email']:
            log.info(f"EMAIL — {a['motor']} ({a['email']}) | {a['name']} → {a['alt_name']} | poupar EUR{a['poupanca']}")
            enviar_email_motorista(
                a['email'], a['motor'], a['data'],
                a['name'], a['price'], a['kwh'],
                a['alt_name'], a['alt_price'], a['alt_dist'],
                a['poupanca'], waze
            )
        # WhatsApp para quem tem telefone
        if a['tel']:
            ok = enviar_whatsapp(a['tel'], msg)
            status = 'OK' if ok else 'ERRO'
            print(f"{status} WA  — {a['motor']} ({a['tel']})")
            log.info(f"WA {status} — {a['motor']} | {a['name']} → {a['alt_name']} | poupar EUR{a['poupanca']}")
            enviados += 1
        elif not a['email']:
            print(f"SEM CONTACTO — {a['motor']}")

        try:
            c3 = sqlite3.connect(DB_ALERTAS)
            c3.execute("INSERT OR IGNORE INTO prio_alertas_enviados (data_transacao, matricula, tipo, enviado_em, alt_station) VALUES (?,?,'imediato',datetime('now'),?)", (a['data_transacao'], a['mat'], a['alt_name']))
            c3.commit()
            c3.close()
        except Exception as e:
            print(f"Erro registo: {e}")
    print(f"\nConcluido — {enviados} WhatsApp enviados")
else:
    print(f"\nPara enviar: python3 /opt/tvde/prio_alertas/alerta_diario.py enviar")
