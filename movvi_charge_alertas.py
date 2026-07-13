#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI CHARGE — Alertas de carregamento (30 min antes da reserva)
================================================================
O que faz:
  · Corre a cada minuto (via threading no servidor principal)
  · 30 min antes de uma reserva, vai ao Cartrack buscar a posição GPS da viatura
  · Calcula a distância ao posto de carregamento (Haversine)
  · Estima o tempo de viagem (velocidade urbana ~28 km/h)
  · Envia WhatsApp + Email ao motorista

Configurar no .env_charge_systemd:
  POSTO_LAT=41.1579          # latitude do parque Movvi (preencher)
  POSTO_LON=-8.6291          # longitude do parque Movvi (preencher)
  POSTO_MORADA=Rua X, Porto  # morada para mostrar no alerta
  CARTRACK_USER=...          # username Cartrack
  CARTRACK_PASS=...          # password Cartrack
  WA_TOKEN=...               # token WhatsApp Business
  WA_PHONE_ID=...            # phone ID WhatsApp Business
  EMAIL_HOST=smtp.gmail.com
  EMAIL_PORT=587
  EMAIL_USER=geral@movvi.pt
  EMAIL_PASS=...
"""
import os, time, math, sqlite3, smtplib, threading, logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import requests

log = logging.getLogger("alertas")

# ─── configuração (lida do ambiente) ─────────────────────────────────────────
POSTO_LAT   = float(os.environ.get("POSTO_LAT", "41.1579"))
POSTO_LON   = float(os.environ.get("POSTO_LON", "-8.6291"))
POSTO_MORADA = os.environ.get("POSTO_MORADA", "Parque Movvi TVDE")
CARTRACK_USER = os.environ.get("CARTRACK_USER", "ADEL00005")
CARTRACK_PASS = os.environ.get("CARTRACK_PASS", os.environ.get("CARTRACK_PASSWORD", ""))
WA_TOKEN      = os.environ.get("WA_TOKEN", "")
WA_PHONE_ID   = os.environ.get("WA_PHONE_ID", "")
EMAIL_HOST    = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT    = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USER    = os.environ.get("EMAIL_USER", "")
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "")
DB_PATH       = "/opt/tvde/movvi_charge.db"
VEL_MEDIA_KMH = 28  # velocidade urbana estimada Porto

# ─── controlo de alertas já enviados ─────────────────────────────────────────
_alertas_enviados = set()  # reserva_id já alertada

# ─── helpers ─────────────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    """Distância em km entre dois pontos GPS."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def tempo_viagem(km):
    """Tempo estimado em minutos."""
    return round((km / VEL_MEDIA_KMH) * 60)

# ─── Cartrack ────────────────────────────────────────────────────────────────
_cartrack_token = {"tok": None, "exp": 0}

def ct_token():
    if _cartrack_token["tok"] and time.time() < _cartrack_token["exp"]:
        return _cartrack_token["tok"]
    r = requests.post("https://api.cartrack.pt/v1/auth/login",
                      json={"username": CARTRACK_USER, "password": CARTRACK_PASS},
                      timeout=15)
    if r.status_code == 200:
        d = r.json()
        _cartrack_token["tok"] = d.get("access_token") or d.get("token")
        _cartrack_token["exp"] = time.time() + 3500
        return _cartrack_token["tok"]
    return None

def posicao_viatura(license_plate):
    """Devolve (lat, lon) da viatura pelo Cartrack, ou None."""
    try:
        import base64
        cred = base64.b64encode(f"{CARTRACK_USER}:{CARTRACK_PASS}".encode()).decode()
        headers = {"Authorization": f"Basic {cred}"}
        r = requests.get("https://fleetapi-pt.cartrack.com/rest/vehicles/status",
                         headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        veiculos = data if isinstance(data, list) else data.get("data", [])
        placa = license_plate.replace("-","").upper()
        veh = next((v for v in veiculos
                    if v.get("registration","").replace("-","").upper() == placa), None)
        if not veh:
            return None
        loc = veh.get("location", {})
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        return (float(lat), float(lon)) if lat and lon else None
    except Exception as e:
        log.error(f"Cartrack error: {e}")
    return None

# ─── notificações ─────────────────────────────────────────────────────────────
def _limpar_numero(phone):
    num = phone.replace("+","").replace(" ","").replace("-","")
    if not num.startswith("351"):
        num = "351" + num
    return num

def enviar_whatsapp_template(phone, nome, hora, posto, km, minutos, maps_url, cancel_url):
    """Envia via template aprovado — funciona sem interacao previa das 24h."""
    if not WA_TOKEN or not WA_PHONE_ID or not phone:
        return False
    num = _limpar_numero(phone)
    km_str = f"{km:.1f}" if km else "?"
    min_str = str(minutos) if minutos else "?"
    try:
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": num,
                "type": "template",
                "template": {
                    "name": "movvi_charge_lembrete",
                    "language": {"code": "pt_PT"},
                    "components": [{
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": nome.split()[0]},
                            {"type": "text", "text": hora},
                            {"type": "text", "text": posto},
                            {"type": "text", "text": km_str},
                            {"type": "text", "text": min_str},
                            {"type": "text", "text": maps_url},
                            {"type": "text", "text": cancel_url},
                        ]
                    }]
                }
            },
            timeout=15)
        ok = r.status_code == 200
        if not ok: log.warning(f"WA template erro {r.status_code}: {r.text[:100]}")
        return ok
    except Exception as e:
        log.error(f"WA template exception: {e}")
        return False

def enviar_whatsapp(phone, mensagem):
    """Envia mensagem WhatsApp livre (fallback — so funciona dentro de 24h)."""
    if not WA_TOKEN or not WA_PHONE_ID or not phone:
        log.info(f"[WA simulado] {phone}: {mensagem[:60]}")
        return False
    num = _limpar_numero(phone)
    try:
        r = requests.post(
            f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": num,
                  "type": "text", "text": {"body": mensagem}},
            timeout=15)
        ok = r.status_code == 200
        if not ok: log.warning(f"WA erro {r.status_code}: {r.text[:100]}")
        return ok
    except Exception as e:
        log.error(f"WA exception: {e}")
        return False

def enviar_email(para, assunto, corpo):
    """Envia email via SMTP."""
    if not EMAIL_USER or not EMAIL_PASS or not para:
        log.info(f"[EMAIL simulado] {para}: {assunto}")
        return False
    try:
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = assunto
        msg["From"]    = f"Movvi Charge <{EMAIL_USER}>"
        msg["To"]      = para
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15) as s:
            s.ehlo(); s.starttls(); s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_USER, [para], msg.as_string())
        return True
    except Exception as e:
        log.error(f"Email exception: {e}")
        return False

# ─── alerta principal ─────────────────────────────────────────────────────────
BASE_URL = os.environ.get("CHARGE_BASE_URL", "https://charge.movvi.com.pt")
MAPS_URL = f"https://www.google.com/maps/dir/?api=1&destination={os.environ.get('POSTO_LAT','41.1187')},{os.environ.get('POSTO_LON','-8.5981')}&travelmode=driving" 

def montar_mensagem(nome, posto, hora_inicio, hora_fim, km, minutos, placa, cancel_token=None):
    cancel_linha = ""
    if cancel_token:
        cancel_linha = f"\n❌ Cancelar reserva: {BASE_URL}/cancelar/{cancel_token}"
    maps_linha = f"\n🗺️ Como chegar: {MAPS_URL}"

    if km is None:
        return (
            f"⚡ Movvi Charge\n"
            f"Olá {nome.split()[0]}!\n\n"
            f"Tens carregamento reservado no {posto} às {hora_inicio} (até {hora_fim}).\n"
            f"Viatura: {placa}\n"
            f"📍 {POSTO_MORADA}\n\n"
            f"Parte a tempo para não perder a tua reserva!"
            f"{cancel_linha}\n\n"
            f"— Movvi Charge · adelmo.pt"
        )
    urgencia = "⚠️ Parte JÁ!" if minutos > 25 else ("🟡 Parte em breve" if minutos > 15 else "✅ Estás perto")
    return (
        f"⚡ Movvi Charge — Lembrete de carregamento\n"
        f"Olá {nome.split()[0]}!\n\n"
        f"Tens carregamento às {hora_inicio} no {posto}.\n"
        f"Viatura: {placa}\n\n"
        f"📍 {POSTO_MORADA}\n"
        f"🚗 Estás a {km:.1f} km do posto\n"
        f"⏱ Tempo estimado: {minutos} min\n\n"
        f"{urgencia}"
        f"{maps_linha}"
        f"{cancel_linha}\n\n"
        f"— Movvi Charge · adelmo.pt"
    )

def verificar_alertas(db_path):
    """Verifica reservas nos próximos 30 min e envia alertas."""
    agora = datetime.now()
    limite = agora + timedelta(minutes=31)
    c = sqlite3.connect(db_path)
    cur = c.execute("""
        SELECT r.id, r.driver_id, r.driver_nome, r.license_plate,
               r.charger_nome, r.inicio, r.fim, r.duracao_min
        FROM reservas r
        WHERE r.estado = 'confirmada'
          AND r.inicio BETWEEN ? AND ?
    """, (agora.strftime("%Y-%m-%dT%H:%M"),
          limite.strftime("%Y-%m-%dT%H:%M")))
    reservas = cur.fetchall()

    # ir buscar emails/telefones dos motoristas ao Movvi (cache simples)
    for res in reservas:
        rid, did, nome, placa, posto, inicio_str, fim_str, dur = res
        if rid in _alertas_enviados:
            continue
        hora_i = inicio_str[11:16]
        hora_f = fim_str[11:16]
        posto_curto = posto.split(" SN")[0] if posto else "Posto"

        # posição GPS via Cartrack
        pos = posicao_viatura(placa) if placa else None
        km_dist, minutos_viagem = None, None
        if pos:
            km_dist = haversine(pos[0], pos[1], POSTO_LAT, POSTO_LON)
            minutos_viagem = tempo_viagem(km_dist)

        # gerar token de cancelamento
        try:
            import secrets, sqlite3 as _sq
            cancel_tok = secrets.token_urlsafe(20)
            _c = _sq.connect(db_path)
            _c.execute("INSERT OR REPLACE INTO cancel_tokens (token, reserva_id) VALUES (?,?)",
                      (cancel_tok, rid))
            _c.commit()
            _c.close()
        except Exception as _e:
            log.error(f"cancel_token erro: {_e}")
            cancel_tok = None
        msg = montar_mensagem(nome, posto_curto, hora_i, hora_f,
                               km_dist, minutos_viagem, placa or "—", cancel_tok)

        # buscar contacto na movvi_charge.db (sync diario da tvde_data.db)
        phone = None
        email = None
        try:
            import sqlite3 as _sq
            c2 = _sq.connect(db_path)
            row = c2.execute("""
                SELECT email, telefone FROM motoristas_contactos
                WHERE movvi_driver_id=? AND ativo=1 LIMIT 1""",
                (str(did),)).fetchone()
            c2.close()
            if row:
                email = row[0] if row[0] else None
                phone = row[1] if row[1] else None
        except Exception as e:
            log.error(f"Erro a buscar contacto: {e}")

        enviou = False
        cancel_url = f"{BASE_URL}/cancelar/{cancel_tok}" if cancel_tok else BASE_URL
        maps_url = MAPS_URL
        if phone:
            # tentar template primeiro (sem restricao 24h), fallback para mensagem livre
            ok_template = enviar_whatsapp_template(
                phone, nome, hora_i, posto_curto,
                km_dist, minutos_viagem, maps_url, cancel_url)
            if not ok_template:
                enviou |= enviar_whatsapp(phone, msg)
            else:
                enviou = True
        if email: enviou |= enviar_email(email, f"⚡ Carregamento às {hora_i} — Movvi Charge", msg)

        if not phone and not email:
            log.warning(f"[ALERTA] sem contacto para {nome} (id={did}) — reserva {rid}")
        else:
            log.info(f"[ALERTA] {nome} · {posto_curto} {hora_i} · {f'{km_dist:.1f}' if km_dist else '?'}km · WA:{phone} Email:{email}")

        _alertas_enviados.add(rid)

_movvi_tok_cache = {"tok": None}
def _get_movvi_token():
    if _movvi_tok_cache["tok"]: return _movvi_tok_cache["tok"]
    MOVVI_EMAIL = os.environ.get("MOVVI_EMAIL","")
    MOVVI_PASS  = os.environ.get("MOVVI_PASS","")
    if not MOVVI_EMAIL: return None
    try:
        r = requests.post("https://movvi.com.pt/api/login",
                          json={"email": MOVVI_EMAIL, "password": MOVVI_PASS},
                          headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"},
                          timeout=15)
        if r.status_code == 200:
            _movvi_tok_cache["tok"] = r.json()["access_token"]
            return _movvi_tok_cache["tok"]
    except: pass
    return None

# ─── loop de monitorização ────────────────────────────────────────────────────

def verificar_noshow(db_path):
    """Marca no-show e liberta o posto se o motorista nao fez check-in 30 min apos o inicio."""
    import sqlite3, requests as req
    agora = datetime.now()
    c = sqlite3.connect(db_path)
    # reservas confirmadas cujo inicio foi ha mais de 30 min (sem check-in)
    rows = c.execute("""
        SELECT id, driver_id, driver_nome, charger_nome, inicio
        FROM reservas
        WHERE estado='confirmada'
        AND datetime(fim) < datetime('now','localtime')
        AND id NOT IN (
            SELECT reserva_id FROM ocpp_sessions
            WHERE estado='ativa'
        )
    """).fetchall()
    for rid, did, nome, posto, inicio in rows:
        # marcar no-show
        c.execute("UPDATE reservas SET estado='no_show' WHERE id=?", (rid,))
        c.commit()
        log.info(f"[NO-SHOW AUTO] {nome} · {posto} · {inicio} — reserva {rid} cancelada")
        # notificar motorista
        try:
            import sqlite3 as _sq
            tvde = _sq.connect("/opt/tvde/tvde_data.db")
            row = tvde.execute("""
                SELECT e.email, e.telefone FROM motoristas m
                JOIN emails_motoristas e ON e.motorista_id = m.id
                WHERE m.movvi_driver_id=? LIMIT 1""", (str(did),)).fetchone()
            tvde.close()
            phone = row[1] if row and row[1] else None
            email = row[0] if row and row[0] else None
            posto_curto = posto.split(" SN")[0] if posto else "Posto"
            hora = inicio[11:16]
            msg = (f"⚠️ Movvi Charge — Reserva cancelada\n\n"
                   f"Ola {nome.split()[0]}!\n\n"
                   f"A tua reserva no {posto_curto} para as {hora} foi cancelada "
                   f"automaticamente por nao comparencia (30 min apos o inicio previsto).\n\n"
                   f"Se precisares de carregar, podes fazer uma nova reserva em:\n"
                   f"{BASE_URL}\n\n"
                   f"— Movvi Charge · adelmo.pt")
            if phone:
                enviar_whatsapp(phone, msg)
            if email:
                enviar_email(email, f"⚠️ Reserva cancelada por nao comparencia — Movvi Charge", msg)
        except Exception as e:
            log.error(f"[NO-SHOW] erro notificacao: {e}")
    c.close()

def iniciar_monitor_alertas():
    """Chamar este função no servidor Flask para arrancar o monitor em background."""
    def _loop():
        log.info("[ALERTAS] Monitor de reservas iniciado")
        while True:
            try:
                verificar_alertas(DB_PATH)
                verificar_noshow(DB_PATH)
            except Exception as e:
                log.error(f"[ALERTAS] erro: {e}")
            time.sleep(60)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
    log.info("Teste manual do sistema de alertas")
    verificar_alertas(DB_PATH)
