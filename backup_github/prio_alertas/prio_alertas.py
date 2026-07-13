#!/usr/bin/env python3
"""
MOVVI TVDE — Sistema de Alertas Prio EV
/opt/tvde/prio_alertas/prio_alertas.py

Dois modos:
  1. IMEDIATO  — corre a cada hora, analisa carregamentos das últimas 2h
                 e avisa o motorista se havia posto mais barato a ≤3km
  2. SEMANAL   — corre às segundas 08:00, envia resumo da semana anterior
                 com total poupado/perdido por motorista

Integração:
  - Lê prio_transacoes (read-only) da BD existente /opt/tvde/tvde_data.db
  - Lê motoristas/emails da BD existente
  - NÃO toca em nenhuma tabela do pipeline principal
  - Escreve apenas nas tabelas prio_alertas_* (próprias)
"""

import sqlite3
import os
import sys
import json
import smtplib
import logging
import requests
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────────
DB_PATH       = "/opt/tvde/tvde_data.db"
DB_ALERTAS    = "/opt/tvde/prio_alertas/alertas.db"
LOG_FILE      = "/opt/tvde/prio_alertas/alertas.log"
CACHE_FILE    = "/opt/tvde/prio_alertas/postos_cache.json"

# Email (mesmo Gmail do weekly_report.py existente)
GMAIL_USER     = "adelmotop10@gmail.com"
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # definir em /etc/environment
EMAIL_GESTOR   = "adelmotop10@gmail.com"

# Meta WhatsApp Business API (mesmo sistema dos outros alertas Movvi)
META_TOKEN    = "EAAVHi2m5MZCkBRh80CqRbemqJKQ7ZCYty4HNubhPvets09vTtkZA6tUzWU6LOdW8xmOZBGxhHFUe9kSCZCSFG6N61MKl8IBc1HEyJ0vRtjKAZBlnZBhU6MapdtZAkcJm4zNWkuKdATlk7yZCyDeWs8TnQukAfmEKEM0ZBF4RmW2LDwGtoKDzXT9tKJaMWAKZBS9E56uhQZDZD"
META_PHONE_ID = "1135522376308599"

# MOBI.E API (postos disponíveis em tempo real)
MOBIE_API_KEY = os.environ.get("MOBIE_API_KEY", "")
MOBIE_URL     = "https://api.mobie.pt/v2/chargers"

# Lógica de alertas
RAIO_KM           = 10.0
MIN_DIFF_EUR_KWH  = 0.05   # só alerta se diferença ≥ 5 cêntimos/kWh
AVG_KWH_SESSAO    = 45.0   # kWh médios por sessão (fallback)

# Operadores excluídos (Continente/Modelo)
OPERADORES_EXCLUIDOS = ["PT*CTN", "PT*MOD"]

# ── LOGGING ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DADOS
# ══════════════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_tabelas_alertas():
    """Cria tabelas próprias do módulo de alertas — nunca toca nas existentes."""
    conn = sqlite3.connect(DB_ALERTAS, timeout=20)
    conn.executescript("""
        -- Mapa de postos com coordenadas (geocodificado automaticamente)
        CREATE TABLE IF NOT EXISTS prio_postos_geo (
            station_name  TEXT PRIMARY KEY,
            lat           REAL,
            lon           REAL,
            geocoded_at   TEXT,
            source        TEXT DEFAULT 'nominatim'
        );

        -- Log de todos os alertas enviados
        CREATE TABLE IF NOT EXISTS prio_alertas_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo            TEXT,      -- 'imediato' | 'semanal'
            matricula       TEXT,
            motorista_nome  TEXT,
            data_transacao  TEXT,
            station_atual   TEXT,
            price_kwh_atual REAL,
            station_alt     TEXT,
            price_kwh_alt   REAL,
            dist_km         REAL,
            kwh             REAL,
            poupanca_eur    REAL,
            whatsapp_ok     INTEGER DEFAULT 0,
            email_ok        INTEGER DEFAULT 0,
            criado_em       TEXT DEFAULT (datetime('now'))
        );

        -- Cache de alertas enviados (evitar repetição na mesma sessão)
        CREATE TABLE IF NOT EXISTS prio_alertas_enviados (
            data_transacao  TEXT,
            matricula       TEXT,
            tipo            TEXT,
            enviado_em      TEXT,
            PRIMARY KEY (data_transacao, matricula, tipo)
        );
    """)
    conn.commit()
    conn.close()
    log.info("Tabelas prio_alertas inicializadas")


def get_transacoes_recentes(horas=2):
    """Carregamentos das últimas N horas — para alertas imediatos."""
    desde = (datetime.now() - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    rows = conn.execute("""
        SELECT p.matricula, p.station_name, p.kwh, p.valor,
               p.data_transacao, p.hora_transacao,
               COALESCE(m.nome, p.matricula) as motorista_nome,
               COALESCE(e.email, '') as email,
               COALESCE(e.telefone, '') as telefone
        FROM prio_transacoes p
        LEFT JOIN viaturas v ON v.matricula = p.matricula
        LEFT JOIN atribuicoes a ON a.viatura_id = v.id
            AND a.data = date(p.data_transacao)
        LEFT JOIN motoristas m ON m.id = a.motorista_id
        LEFT JOIN emails_motoristas e ON e.motorista_id = m.id
        WHERE p.data_transacao >= ?
        ORDER BY p.data_transacao DESC
    """, (desde,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transacoes_semana(inicio, fim):
    """Todas as transações de um período — para resumo semanal."""
    conn = get_db()
    rows = conn.execute("""
        SELECT p.matricula, p.station_name, p.kwh, p.valor,
               p.data_transacao,
               COALESCE(m.nome, p.matricula) as motorista_nome,
               COALESCE(e.email, '') as email,
               COALESCE(e.telefone, '') as telefone
        FROM prio_transacoes p
        LEFT JOIN viaturas v ON v.matricula = p.matricula
        LEFT JOIN atribuicoes a ON a.viatura_id = v.id
            AND a.data = date(p.data_transacao)
        LEFT JOIN motoristas m ON m.id = a.motorista_id
        LEFT JOIN emails_motoristas e ON e.motorista_id = m.id
        WHERE p.data_transacao >= ? AND p.data_transacao <= ?
        ORDER BY p.matricula, p.data_transacao
    """, (inicio, fim)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ja_enviado(data_transacao, matricula, tipo):
    conn = sqlite3.connect(DB_ALERTAS, timeout=20)
    conn.row_factory = sqlite3.Row
    r = conn.execute("""
        SELECT 1 FROM prio_alertas_enviados
        WHERE data_transacao=? AND matricula=? AND tipo=?
    """, (data_transacao, matricula, tipo)).fetchone()
    conn.close()
    return r is not None


def marcar_enviado(data_transacao, matricula, tipo):
    conn = sqlite3.connect(DB_ALERTAS, timeout=20)
    conn.execute("""
        INSERT OR IGNORE INTO prio_alertas_enviados
        (data_transacao, matricula, tipo, enviado_em)
        VALUES (?,?,?,datetime('now'))
    """, (data_transacao, matricula, tipo))
    conn.commit()
    conn.close()


def log_alerta(dados):
    conn = sqlite3.connect(DB_ALERTAS, timeout=20)
    conn.execute("""
        INSERT INTO prio_alertas_log
        (tipo, matricula, motorista_nome, data_transacao, station_atual,
         price_kwh_atual, station_alt, price_kwh_alt, dist_km, kwh,
         poupanca_eur, whatsapp_ok, email_ok)
        VALUES (:tipo,:matricula,:motorista_nome,:data_transacao,:station_atual,
                :price_kwh_atual,:station_alt,:price_kwh_alt,:dist_km,:kwh,
                :poupanca_eur,:whatsapp_ok,:email_ok)
    """, dados)
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# GEOCODING — descobrir coordenadas dos postos pelo nome
# ══════════════════════════════════════════════════════════════════════════════

def get_coordenadas_posto(station_name):
    """
    Devolve (lat, lon) de um posto.
    1. Tenta a cache local (tabela prio_postos_geo)
    2. Se não encontrar, geocodifica via Nominatim (OpenStreetMap) — gratuito
    """
    conn = sqlite3.connect(DB_ALERTAS, timeout=20)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT lat, lon FROM prio_postos_geo WHERE station_name=?",
        (station_name,)
    ).fetchone()
    conn.close()

    if row and row["lat"]:
        return row["lat"], row["lon"]

    # Geocodificar — Nominatim pede user-agent identificado
    try:
        query = f"{station_name} Portugal"
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "pt"},
            headers={"User-Agent": "MovviTVDE/1.0 adelmotop10@gmail.com"},
            timeout=10
        )
        r.raise_for_status()
        results = r.json()
        time.sleep(1)  # respeitar rate limit Nominatim (1 req/seg)

        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            display_name = results[0].get("display_name", "")

            # Guardar na cache
            conn = sqlite3.connect(DB_ALERTAS, timeout=20)
            conn.execute("""
                INSERT OR REPLACE INTO prio_postos_geo
                (station_name, lat, lon, geocoded_at, display_name)
                VALUES (?,?,?,datetime('now'),?)
            """, (station_name, lat, lon, display_name))
            conn.commit()
            conn.close()

            log.info(f"Geocodificado: {station_name} → {lat},{lon}")
            return lat, lon

    except Exception as e:
        log.warning(f"Geocoding falhou para '{station_name}': {e}")

    return None, None


def distancia_km(lat1, lon1, lat2, lon2):
    """Haversine — distância em km entre dois pontos."""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


# ══════════════════════════════════════════════════════════════════════════════
# MOBI.E — postos mais baratos na área
# ══════════════════════════════════════════════════════════════════════════════

def buscar_alternativa_mobie(lat, lon, price_kwh_atual):
    """
    Consulta MOBI.E para postos CCS disponíveis num raio de 3km.
    Devolve o posto mais barato com diferença ≥ MIN_DIFF_EUR_KWH, ou None.
    """
    if not MOBIE_API_KEY:
        log.warning("MOBIE_API_KEY não configurada — a usar postos da BD histórica")
        return buscar_alternativa_historico(lat, lon, price_kwh_atual)

    try:
        r = requests.get(MOBIE_URL, params={
            "latitude":  lat,
            "longitude": lon,
            "radius_km": RAIO_KM,
            "connector": "CCS",
            "available": "true",
        }, headers={
            "Authorization": f"Bearer {MOBIE_API_KEY}",
            "Content-Type": "application/json",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()

        candidatos = []
        for c in data.get("chargers", []):
            evse_id = c.get("evse_id", "")
            # Excluir Continente/Modelo
            if any(evse_id.startswith(p) for p in OPERADORES_EXCLUIDOS):
                continue
            price = float(c.get("price_kwh") or 0)
            if price <= 0:
                continue
            diff = price_kwh_atual - price
            if diff < MIN_DIFF_EUR_KWH:
                continue
            dist = distancia_km(lat, lon,
                                 float(c.get("latitude", 0)),
                                 float(c.get("longitude", 0)))
            if dist > RAIO_KM:
                continue

            candidatos.append({
                "name":       c.get("name") or c.get("station_name", "Posto desconhecido"),
                "lat":        float(c.get("latitude", 0)),
                "lon":        float(c.get("longitude", 0)),
                "price_kwh":  price,
                "power_kw":   float(c.get("max_power_kw") or 0),
                "dist_km":    round(dist, 2),
                "diff_kwh":   round(diff, 3),
                "maps_url":   f"https://www.google.com/maps/search/?api=1&query={c.get('latitude')},{c.get('longitude')}",
                "waze_url":   f"https://waze.com/ul?ll={c.get('latitude')},{c.get('longitude')}&navigate=yes",
            })

        if not candidatos:
            return None

        # Ordenar: mais barato primeiro
        return sorted(candidatos, key=lambda x: x["price_kwh"])[0]

    except Exception as e:
        log.error(f"MOBI.E API erro: {e}")
        return buscar_alternativa_historico(lat, lon, price_kwh_atual)


def buscar_alternativa_historico(lat, lon, price_kwh_atual):
    """
    Fallback: usa postos já conhecidos na BD histórica (prio_transacoes)
    com tarifa mais baixa registada, e verifica se estão em 3km.
    """
    if lat is None:
        return None

    conn = get_db()
    # Postos com tarifa média mais baixa nos últimos 30 dias
    rows = conn.execute("""
        SELECT station_name,
               AVG(valor/kwh) as avg_price,
               SUM(kwh) as total_kwh
        FROM prio_transacoes
        WHERE kwh > 0 AND valor > 0
          AND data_transacao >= date('now','-30 days')
        GROUP BY station_name
        HAVING avg_price < ?
        ORDER BY avg_price ASC
        LIMIT 20
    """, (price_kwh_atual - MIN_DIFF_EUR_KWH,)).fetchall()
    conn.close()

    for row in rows:
        station = row["station_name"]
        avg_p   = round(row["avg_price"], 3)
        lat2, lon2 = get_coordenadas_posto(station)
        if lat2 is None:
            continue
        dist = distancia_km(lat, lon, lat2, lon2)
        if dist <= RAIO_KM:
            return {
                "name":      station,
                "lat":       lat2,
                "lon":       lon2,
                "price_kwh": avg_p,
                "power_kw":  0,
                "dist_km":   round(dist, 2),
                "diff_kwh":  round(price_kwh_atual - avg_p, 3),
                "maps_url":  f"https://www.google.com/maps/search/?api=1&query={lat2},{lon2}",
                "waze_url":  f"https://waze.com/ul?ll={lat2},{lon2}&navigate=yes",
                "fonte":     "historico",
            }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ALERTAS — WhatsApp e Email
# ══════════════════════════════════════════════════════════════════════════════

def enviar_whatsapp(telefone, mensagem):
    """Envia WhatsApp via Meta Business API — mesmo sistema dos alertas Movvi."""
    if not telefone:
        log.warning("WhatsApp: sem telefone para motorista")
        return False
    # Normalizar número: remover +, espaços, garantir prefixo 351
    tel = telefone.replace("+","").replace(" ","").strip()
    if not tel:
        return False
    try:
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {META_TOKEN}",
                     "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp",
                  "to": tel,
                  "type": "text",
                  "text": {"body": mensagem}},
            timeout=15
        )
        r.raise_for_status()
        log.info(f"WhatsApp Meta enviado para {tel}")
        return True
    except Exception as e:
        log.error(f"WhatsApp Meta erro para {tel}: {e} | resp: {getattr(e, 'response', None) and e.response.text}")
        return False


def enviar_email(para, assunto, html):
    if not GMAIL_PASSWORD or not para:
        log.warning(f"Email: sem password ou destinatário ({para})")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = GMAIL_USER
        msg["To"]      = para
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587) as srv:
            srv.starttls()
            srv.login(GMAIL_USER, GMAIL_PASSWORD)
            srv.sendmail(GMAIL_USER, para, msg.as_string())
        log.info(f"Email enviado para {para}: {assunto}")
        return True
    except Exception as e:
        log.error(f"Email erro para {para}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MODO 1 — ALERTA IMEDIATO (corre a cada hora)
# ══════════════════════════════════════════════════════════════════════════════

def processar_alertas_imediatos():
    """
    Analisa carregamentos das últimas 2 horas.
    Para cada um, verifica se havia posto mais barato em 3km.
    Se sim, envia WhatsApp ao motorista e email ao gestor.
    """
    log.info("=== ALERTAS IMEDIATOS — início ===")
    transacoes = get_transacoes_recentes(horas=2)

    if not transacoes:
        log.info("Sem carregamentos nas últimas 2h")
        return

    alertas_enviados = 0

    for t in transacoes:
        matricula      = t["matricula"]
        data_t         = t["data_transacao"]
        station_name   = t["station_name"]
        kwh            = float(t["kwh"] or AVG_KWH_SESSAO)
        valor          = float(t["valor"] or 0)
        price_kwh      = round(valor / kwh, 4) if kwh > 0 else 0
        motorista      = t["motorista_nome"]
        email          = t["email"]
        telefone       = t["telefone"]

        if price_kwh <= 0:
            continue

        # Evitar reenvio
        if ja_enviado(data_t, matricula, "imediato"):
            continue

        # Coordenadas do posto actual
        lat, lon = get_coordenadas_posto(station_name)

        # Procurar alternativa mais barata
        alt = buscar_alternativa_mobie(lat, lon, price_kwh) if lat else None

        # Marcar como processado (mesmo sem alternativa)
        marcar_enviado(data_t, matricula, "imediato")

        if not alt:
            log.info(f"{matricula} | {station_name} | €{price_kwh:.3f}/kWh — sem alternativa")
            continue

        poupanca = round(alt["diff_kwh"] * kwh, 2)
        dist_str = f"{int(alt['dist_km']*1000)}m" if alt["dist_km"] < 1 else f"{alt['dist_km']:.1f}km"

        log.info(f"ALERTA: {matricula} | {station_name} €{price_kwh:.3f} → {alt['name']} €{alt['price_kwh']:.3f} | poupar €{poupanca}")

        # ── WhatsApp ao motorista ─────────────────────────────────────────
        custo_aqui = round(price_kwh * kwh, 2)
        custo_alt  = round(alt["price_kwh"] * kwh, 2)
        nome_curto = motorista.split()[0] if motorista else "Motorista"

        msg_wa = (
            f"⚡ *Alerta carregamento Movvi*\n\n"
            f"{nome_curto}, acabaste de carregar em *{station_name}* a *€{price_kwh:.3f}/kWh*.\n\n"
            f"A *{dist_str}* estava disponível *{alt['name']}* a *€{alt['price_kwh']:.3f}/kWh*.\n\n"
            f"Para os teus {kwh:.0f} kWh:\n"
            f"• Aqui: ~€{custo_aqui}\n"
            f"• {alt['name']}: ~€{custo_alt}\n"
            f"• 💰 Diferença: *€{poupanca}*\n\n"
            f"📍 Na próxima vez:\n"
            f"Waze → {alt['waze_url']}\n"
            f"Maps → {alt['maps_url']}"
        )

        wa_ok = enviar_whatsapp(telefone, msg_wa) if telefone else False

        # ── Email ao gestor ───────────────────────────────────────────────
        html_gestor = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
          <div style="background:#1a1a2e;padding:20px;border-radius:8px 8px 0 0">
            <h2 style="color:#00d4aa;margin:0">⚡ Alerta Carregamento — {motorista}</h2>
            <p style="color:#aaa;margin:4px 0 0">{data_t} · {matricula}</p>
          </div>
          <div style="background:#f8f9fa;padding:20px">
            <table style="width:100%;border-collapse:collapse">
              <tr style="background:#fff">
                <td style="padding:12px;border:1px solid #dee2e6"><strong>Posto utilizado</strong><br>{station_name}</td>
                <td style="padding:12px;border:1px solid #dee2e6;text-align:right"><strong style="color:#e74c3c">€{price_kwh:.3f}/kWh</strong></td>
              </tr>
              <tr style="background:#e8f5e9">
                <td style="padding:12px;border:1px solid #dee2e6"><strong>Alternativa disponível</strong><br>{alt['name']} ({dist_str})</td>
                <td style="padding:12px;border:1px solid #dee2e6;text-align:right"><strong style="color:#27ae60">€{alt['price_kwh']:.3f}/kWh</strong></td>
              </tr>
            </table>
            <div style="background:#fff3cd;padding:15px;margin-top:15px;border-radius:6px;border-left:4px solid #ffc107">
              <strong>💰 Poupança potencial desta sessão: €{poupanca}</strong><br>
              <small>{kwh:.0f} kWh × €{alt['diff_kwh']:.3f} diferença</small>
            </div>
            <p style="margin-top:15px">
              <a href="{alt['waze_url']}" style="background:#00d4aa;color:#000;padding:8px 16px;border-radius:4px;text-decoration:none;margin-right:8px">Abrir Waze</a>
              <a href="{alt['maps_url']}" style="background:#4285f4;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none">Abrir Maps</a>
            </p>
          </div>
        </div>"""

        email_ok = enviar_email(
            EMAIL_GESTOR,
            f"⚡ Alerta Prio — {motorista} ({matricula}) — poupar €{poupanca}",
            html_gestor
        )

        # Log na BD
        log_alerta({
            "tipo": "imediato", "matricula": matricula, "motorista_nome": motorista,
            "data_transacao": data_t, "station_atual": station_name,
            "price_kwh_atual": price_kwh, "station_alt": alt["name"],
            "price_kwh_alt": alt["price_kwh"], "dist_km": alt["dist_km"],
            "kwh": kwh, "poupanca_eur": poupanca,
            "whatsapp_ok": int(wa_ok), "email_ok": int(email_ok)
        })
        alertas_enviados += 1

    log.info(f"=== ALERTAS IMEDIATOS — {alertas_enviados} enviados ===")
    return alertas_enviados


# ══════════════════════════════════════════════════════════════════════════════
# MODO 2 — RESUMO SEMANAL (corre às segundas 08:00)
# ══════════════════════════════════════════════════════════════════════════════

def processar_resumo_semanal():
    """
    Analisa todos os carregamentos da semana anterior (seg-dom).
    Calcula quanto cada motorista pagou vs quanto pagaria no posto mais barato.
    Envia email personalizado a cada motorista e resumo geral ao gestor.
    """
    log.info("=== RESUMO SEMANAL — início ===")

    # Semana anterior: segunda a domingo
    hoje    = datetime.now()
    fim     = (hoje - timedelta(days=hoje.weekday() + 1)).replace(hour=23, minute=59, second=59)
    inicio  = fim - timedelta(days=6)
    inicio_str = inicio.strftime("%Y-%m-%d")
    fim_str    = fim.strftime("%Y-%m-%d")
    semana_label = f"{inicio.strftime('%d/%m')} a {fim.strftime('%d/%m/%Y')}"

    log.info(f"Período: {semana_label}")

    transacoes = get_transacoes_semana(inicio_str + " 00:00:00", fim_str + " 23:59:59")

    if not transacoes:
        log.info("Sem transações na semana anterior")
        return

    # Agrupar por motorista
    por_motorista = defaultdict(list)
    for t in transacoes:
        por_motorista[t["matricula"]].append(t)

    resumo_geral = []
    total_poupanca_frota = 0

    for matricula, sessoes in por_motorista.items():
        motorista = sessoes[0]["motorista_nome"]
        email     = sessoes[0]["email"]
        telefone  = sessoes[0]["telefone"]

        detalhes_sessoes = []
        total_kwh      = 0
        total_pago     = 0
        total_poupanca = 0

        for s in sessoes:
            kwh    = float(s["kwh"] or AVG_KWH_SESSAO)
            valor  = float(s["valor"] or 0)
            price  = round(valor / kwh, 4) if kwh > 0 else 0
            if price <= 0:
                continue

            lat, lon = get_coordenadas_posto(s["station_name"])
            alt      = buscar_alternativa_mobie(lat, lon, price) if lat else None

            total_kwh  += kwh
            total_pago += valor

            sessao_info = {
                "data":        s["data_transacao"][:16],
                "posto":       s["station_name"],
                "kwh":         kwh,
                "valor":       valor,
                "price_kwh":   price,
                "alternativa": alt,
                "poupanca":    round(alt["diff_kwh"] * kwh, 2) if alt else 0,
            }
            total_poupanca += sessao_info["poupanca"]
            detalhes_sessoes.append(sessao_info)

        if not detalhes_sessoes:
            continue

        total_poupanca_frota += total_poupanca

        # ── WhatsApp resumo ao motorista ──────────────────────────────────
        nome_curto = motorista.split()[0] if motorista else "Motorista"
        n_sessoes  = len(detalhes_sessoes)
        n_com_alt  = sum(1 for s in detalhes_sessoes if s["alternativa"])

        linhas_wa = [
            f"📊 *Resumo semanal Movvi — {semana_label}*\n",
            f"Olá {nome_curto}! Esta semana fizeste *{n_sessoes} carregamento{'s' if n_sessoes>1 else ''}*.\n",
        ]

        if total_poupanca > 0:
            linhas_wa.append(
                f"💰 *Se tivesses ido ao posto mais barato em {n_com_alt} {'sessões' if n_com_alt>1 else 'sessão'}, "
                f"terias poupado €{total_poupanca:.2f}* esta semana.\n"
            )
            # Detalhar as 3 maiores poupanças
            top = sorted([s for s in detalhes_sessoes if s["alternativa"]],
                         key=lambda x: x["poupanca"], reverse=True)[:3]
            for s in top:
                alt = s["alternativa"]
                dist_str = f"{int(alt['dist_km']*1000)}m" if alt["dist_km"] < 1 else f"{alt['dist_km']:.1f}km"
                linhas_wa.append(
                    f"• {s['data'][:10]} — {s['posto']}: em vez disso *{alt['name']}* "
                    f"a {dist_str} teria poupado *€{s['poupanca']:.2f}*"
                )
        else:
            linhas_wa.append("✅ Esta semana escolheste sempre o posto mais económico disponível!")

        linhas_wa.append(f"\nTotal: *{total_kwh:.0f} kWh | €{total_pago:.2f}*")
        msg_wa = "\n".join(linhas_wa)

        wa_ok = enviar_whatsapp(telefone, msg_wa) if telefone else False

        # ── Email detalhado ao motorista ──────────────────────────────────
        rows_html = ""
        for s in detalhes_sessoes:
            alt = s["alternativa"]
            if alt:
                alt_cell = (f"<td style='color:#27ae60'>{alt['name']}<br>"
                            f"<small>€{alt['price_kwh']:.3f}/kWh · {alt['dist_km']:.1f}km</small></td>"
                            f"<td style='color:#e74c3c;text-align:right'><strong>€{s['poupanca']:.2f}</strong></td>")
            else:
                alt_cell = "<td style='color:#999'>✅ Melhor disponível</td><td>—</td>"

            rows_html += f"""
            <tr>
              <td style='padding:8px;border:1px solid #dee2e6'>{s['data']}</td>
              <td style='padding:8px;border:1px solid #dee2e6'>{s['posto']}</td>
              <td style='padding:8px;border:1px solid #dee2e6;text-align:right'>{s['kwh']:.0f} kWh</td>
              <td style='padding:8px;border:1px solid #dee2e6;text-align:right'>€{s['price_kwh']:.3f}</td>
              {alt_cell}
            </tr>"""

        html_motorista = f"""
        <div style="font-family:Arial,sans-serif;max-width:660px;margin:auto">
          <div style="background:#1a1a2e;padding:20px;border-radius:8px 8px 0 0">
            <h2 style="color:#00d4aa;margin:0">Resumo semanal Movvi ⚡</h2>
            <p style="color:#aaa;margin:4px 0 0">{semana_label} · {motorista} · {matricula}</p>
          </div>
          <div style="background:#f8f9fa;padding:20px">
            {"<div style='background:#fff3cd;padding:15px;border-radius:6px;border-left:4px solid #ffc107;margin-bottom:20px'><strong>💰 Poupança potencial da semana: €" + f"{total_poupanca:.2f}" + "</strong><br><small>Valor que terias poupado se tivesses ido ao posto mais barato disponível em 3km</small></div>" if total_poupanca > 0 else "<div style='background:#d4edda;padding:15px;border-radius:6px;border-left:4px solid #28a745;margin-bottom:20px'><strong>✅ Óptima semana! Escolheste sempre o posto mais económico disponível.</strong></div>"}
            <table style="width:100%;border-collapse:collapse;font-size:13px">
              <tr style="background:#1a1a2e;color:#fff">
                <th style="padding:10px;text-align:left">Data</th>
                <th style="padding:10px;text-align:left">Posto</th>
                <th style="padding:10px;text-align:right">kWh</th>
                <th style="padding:10px;text-align:right">€/kWh</th>
                <th style="padding:10px;text-align:left">Alternativa</th>
                <th style="padding:10px;text-align:right">Diferença</th>
              </tr>
              {rows_html}
              <tr style="background:#e9ecef;font-weight:bold">
                <td colspan="2" style="padding:10px;border:1px solid #dee2e6">TOTAL</td>
                <td style="padding:10px;border:1px solid #dee2e6;text-align:right">{total_kwh:.0f} kWh</td>
                <td style="padding:10px;border:1px solid #dee2e6;text-align:right">€{(total_pago/total_kwh):.3f}</td>
                <td style="padding:10px;border:1px solid #dee2e6"></td>
                <td style="padding:10px;border:1px solid #dee2e6;text-align:right;color:#e74c3c">€{total_poupanca:.2f}</td>
              </tr>
            </table>
            <p style="font-size:12px;color:#999;margin-top:20px">
              Alternativas calculadas com base na rede MOBI.E (raio 3km, conector CCS, excluindo Continente/Modelo).
            </p>
          </div>
        </div>"""

        email_ok = enviar_email(
            email or EMAIL_GESTOR,
            f"📊 Resumo semanal Prio — {semana_label} — {motorista}",
            html_motorista
        )

        resumo_geral.append({
            "motorista": motorista, "matricula": matricula,
            "sessoes": n_sessoes, "kwh": total_kwh,
            "pago": total_pago, "poupanca": total_poupanca,
            "wa_ok": wa_ok, "email_ok": email_ok
        })
        log.info(f"{motorista} ({matricula}): {n_sessoes} sessões, €{total_poupanca:.2f} poupança potencial")

    # ── Email resumo geral ao gestor ──────────────────────────────────────
    rows_gestor = ""
    for r in sorted(resumo_geral, key=lambda x: x["poupanca"], reverse=True):
        rows_gestor += f"""
        <tr>
          <td style='padding:8px;border:1px solid #dee2e6'>{r['motorista']}</td>
          <td style='padding:8px;border:1px solid #dee2e6'>{r['matricula']}</td>
          <td style='padding:8px;border:1px solid #dee2e6;text-align:right'>{r['sessoes']}</td>
          <td style='padding:8px;border:1px solid #dee2e6;text-align:right'>{r['kwh']:.0f}</td>
          <td style='padding:8px;border:1px solid #dee2e6;text-align:right'>€{r['pago']:.2f}</td>
          <td style='padding:8px;border:1px solid #dee2e6;text-align:right;color:#e74c3c;font-weight:bold'>€{r['poupanca']:.2f}</td>
        </tr>"""

    html_gestor = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto">
      <div style="background:#1a1a2e;padding:20px;border-radius:8px 8px 0 0">
        <h2 style="color:#00d4aa;margin:0">Relatório semanal Prio — Frota Movvi</h2>
        <p style="color:#aaa;margin:4px 0 0">{semana_label}</p>
      </div>
      <div style="background:#f8f9fa;padding:20px">
        <div style="background:#fff3cd;padding:15px;border-radius:6px;border-left:4px solid #ffc107;margin-bottom:20px">
          <strong>💰 Poupança potencial total da frota: €{total_poupanca_frota:.2f}</strong>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#1a1a2e;color:#fff">
            <th style="padding:10px;text-align:left">Motorista</th>
            <th style="padding:10px;text-align:left">Matrícula</th>
            <th style="padding:10px;text-align:right">Sessões</th>
            <th style="padding:10px;text-align:right">kWh</th>
            <th style="padding:10px;text-align:right">Pago</th>
            <th style="padding:10px;text-align:right">Poderia poupar</th>
          </tr>
          {rows_gestor}
        </table>
      </div>
    </div>"""

    enviar_email(
        EMAIL_GESTOR,
        f"📊 Relatório semanal Prio Movvi — {semana_label} — poupar €{total_poupanca_frota:.2f}",
        html_gestor
    )
    log.info(f"=== RESUMO SEMANAL concluído — poupança total frota: €{total_poupanca_frota:.2f} ===")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_tabelas_alertas()

    modo = sys.argv[1] if len(sys.argv) > 1 else "imediato"

    if modo == "semanal":
        processar_resumo_semanal()
    elif modo == "imediato":
        processar_alertas_imediatos()
    else:
        print(f"Uso: python3 prio_alertas.py [imediato|semanal]")
        sys.exit(1)
