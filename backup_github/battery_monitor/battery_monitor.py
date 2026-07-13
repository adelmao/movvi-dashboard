#!/usr/bin/env python3
"""
TVDE Fleet — Monitor de Bateria EV
Movvi Fleet Intelligence | /opt/tvde/battery_monitor.py

- Lê motorista de hoje directamente da tvde_data.db (atribuicoes + emails_motoristas)
- Alerta WhatsApp via Meta Business API (mesmo sistema Prio/trânsito/tempo)
- Alerta Email via Gmail
- Cria Notificação na plataforma Cartrack
- Cooldown 2h por viatura para evitar spam
- Log em /opt/tvde/logs/battery_monitor.log
"""

import os, sys, json, base64, logging, smtplib, sqlite3, requests
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─────────────────────────────────────────────────────
# CONFIGURAÇÃO — variáveis já em /etc/environment no VPS
# ─────────────────────────────────────────────────────

DB_PATH            = "/opt/tvde/tvde_data.db"

# Cartrack — Portugal
CARTRACK_USERNAME  = "ADEL00005"
CARTRACK_PASSWORD  = os.environ.get("CARTRACK_PASSWORD", "")
CARTRACK_BASE_URL  = "https://fleetapi-pt.cartrack.com/rest"

# Meta WhatsApp Business API (mesmo que Prio / trânsito / tempo)
META_TOKEN         = os.environ.get("META_TOKEN", "")
META_PHONE_ID      = os.environ.get("META_PHONE_ID", "")

# Email
GMAIL_USER         = "adelmotop10@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_GESTOR       = "adelmotop10@gmail.com"

# Thresholds
THRESHOLD_CRITICO  = 20    # % → alerta motorista + gestor + Cartrack
THRESHOLD_AVISO    = 30    # % → só alerta gestor (aviso antecipado)
COOLDOWN_HORAS     = 2

ALERT_LOG_PATH     = Path("/opt/tvde/logs/battery_alerts_sent.json")
LOG_PATH           = "/opt/tvde/logs/battery_monitor.log"

# ─────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────
Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# BASE DE DADOS — busca motorista atribuído hoje
# ─────────────────────────────────────────────────────

def get_motorista_da_viatura(matricula: str, data: str = None) -> dict:
    """
    Devolve {nome, email, telefone} do motorista atribuído à matrícula hoje.
    Usa a mesma lógica do weekly_report.py e prio_alertas.py.
    Fallback: nome='Sem motorista', email='', telefone=''
    """
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")

    fallback = {"nome": "Sem motorista atribuído", "email": "", "telefone": ""}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT
                m.nome,
                COALESCE(em.email, '')    AS email,
                COALESCE(em.telefone, '') AS telefone
            FROM viaturas v
            JOIN atribuicoes a
                ON a.viatura_id = v.id
               AND a.data = ?
            JOIN motoristas m
                ON m.id = a.motorista_id
            LEFT JOIN emails_motoristas em
                ON em.motorista_id = m.id
               AND em.activo = 1
            WHERE v.matricula = ?
            LIMIT 1
        """, (data, matricula)).fetchone()
        conn.close()

        if row:
            return {"nome": row["nome"], "email": row["email"], "telefone": row["telefone"]}
        return fallback

    except Exception as e:
        log.warning(f"BD erro ao buscar motorista de {matricula}: {e}")
        return fallback

# ─────────────────────────────────────────────────────
# CARTRACK API
# ─────────────────────────────────────────────────────

def _ct_headers():
    cred = base64.b64encode(f"{CARTRACK_USERNAME}:{CARTRACK_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Accept": "application/json",
            "Content-Type": "application/json"}

def fetch_vehicle_status():
    """
    GET /vehicles/status — bateria + ignição + charging + velocidade num único endpoint.
    Só alerta se em movimento (ignition=true ou speed>0) e não está a carregar.
    """
    try:
        r = requests.get(f"{CARTRACK_BASE_URL}/vehicles/status?limit=200",
                         headers=_ct_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
        lista = data if isinstance(data, list) else data.get("vehicles", data.get("data", []))
        result = {}
        for v in lista:
            reg = (v.get("registration") or "").strip()
            if not reg:
                continue
            elec   = v.get("electric") or {}
            soc    = elec.get("battery_percentage_left")
            status = (elec.get("charging_status") or "UNPLUGGED").upper()
            ignition     = bool(v.get("ignition"))
            speed        = float(v.get("speed") or 0)
            em_movimento = ignition or speed > 0
            result[reg]  = {
                "soc":          soc,
                "charging":     status == "CHARGING",
                "em_movimento": em_movimento,
                "ignition":     ignition,
                "speed":        speed,
            }
        log.info(f"Cartrack status: {len(result)} viaturas")
        return result
    except Exception as e:
        log.error(f"Cartrack status: {e}")
        return None

def criar_notificacao_cartrack(matricula, soc, nome):
    try:
        payload = {
            "type": "BATTERY_LOW",
            "title": f"⚠️ Bateria Crítica — {matricula}",
            "message": (f"Viatura {matricula} ({nome}) com {soc:.0f}% de bateria. "
                        f"Necessário recarregar urgentemente."),
            "priority": "HIGH",
            "vehicleRegistration": matricula
        }
        r = requests.post(f"{CARTRACK_BASE_URL}/notifications",
                          headers=_ct_headers(), json=payload, timeout=15)
        if r.status_code in [200, 201, 204]:
            log.info(f"✅ Notificação Cartrack criada: {matricula}")
        else:
            log.warning(f"Cartrack notification {r.status_code}: {r.text[:150]}")
    except Exception as e:
        log.warning(f"Cartrack notification: {e}")

def normalizar_viaturas(data):
    if isinstance(data, list):
        return data
    return data.get("vehicles", data.get("data", data.get("results", [])))

# ─────────────────────────────────────────────────────
# WHATSAPP — Meta Business API (igual ao Prio/trânsito)
# ─────────────────────────────────────────────────────

def enviar_whatsapp(telefone: str, mensagem: str) -> bool:
    if not META_TOKEN or not META_PHONE_ID:
        log.warning("WhatsApp: META_TOKEN/META_PHONE_ID não configurado")
        return False
    if not telefone:
        return False
    tel = telefone.replace("+", "").replace(" ", "").strip()
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
        log.info(f"✅ WhatsApp Meta → {tel}")
        return True
    except Exception as e:
        resp = getattr(getattr(e, "response", None), "text", "")
        log.error(f"❌ WhatsApp {tel}: {e} | {resp[:200]}")
        return False

# ─────────────────────────────────────────────────────
# EMAIL — Gmail SMTP
# ─────────────────────────────────────────────────────

def enviar_email(para: str, assunto: str, html: str) -> bool:
    if not GMAIL_APP_PASSWORD or not para:
        log.warning(f"Email: sem config ou sem destinatário ({para})")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = GMAIL_USER
        msg["To"]      = para
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        log.info(f"✅ Email → {para}")
        return True
    except Exception as e:
        log.error(f"❌ Email {para}: {e}")
        return False

def html_alerta(nome, matricula, soc, hora):
    cor = "#dc3545" if soc <= 20 else "#fd7e14"
    nivel = "CRÍTICA" if soc <= 20 else "BAIXA"
    return f"""
<html><body style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
<div style="background:#111827;padding:18px 24px;border-radius:8px 8px 0 0">
  <p style="color:#9ca3af;font-size:11px;margin:0 0 4px;letter-spacing:.05em">MOVVI FLEET INTELLIGENCE</p>
  <h2 style="color:#fff;margin:0;font-size:19px">⚠️ Alerta de Bateria {nivel}</h2>
</div>
<div style="border:1px solid #e5e7eb;border-top:none;padding:20px 24px;border-radius:0 0 8px 8px">
  <p style="margin:0 0 14px">Olá <strong>{nome}</strong>,</p>
  <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:14px">
    <tr style="background:#f9fafb">
      <td style="padding:9px 12px;border:1px solid #e5e7eb;font-weight:600;width:38%">Viatura</td>
      <td style="padding:9px 12px;border:1px solid #e5e7eb">{matricula}</td>
    </tr>
    <tr>
      <td style="padding:9px 12px;border:1px solid #e5e7eb;font-weight:600">Bateria</td>
      <td style="padding:9px 12px;border:1px solid #e5e7eb;color:{cor};font-weight:700;font-size:18px">{soc:.0f}%</td>
    </tr>
    <tr style="background:#f9fafb">
      <td style="padding:9px 12px;border:1px solid #e5e7eb;font-weight:600">Hora</td>
      <td style="padding:9px 12px;border:1px solid #e5e7eb">{hora}</td>
    </tr>
  </table>
  <p style="background:#fef2f2;border-left:4px solid {cor};border-radius:0;padding:10px 14px;color:#7f1d1d;font-size:13px;margin:0 0 14px">
    <strong>Por favor recarregue a viatura o mais rapidamente possível.</strong>
  </p>
  <p style="color:#6b7280;font-size:11px;margin:0">Movvi Fleet Intelligence · Alertas automáticos de bateria EV</p>
</div></body></html>"""

# ─────────────────────────────────────────────────────
# COOLDOWN — anti-spam
# ─────────────────────────────────────────────────────

def carregar_historico():
    if ALERT_LOG_PATH.exists():
        with open(ALERT_LOG_PATH) as f:
            return json.load(f)
    return {}

def guardar_historico(h):
    ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "w") as f:
        json.dump(h, f, indent=2)

def pode_alertar(reg, historico):
    if reg not in historico:
        return True
    ultimo = datetime.fromisoformat(historico[reg])
    return datetime.now() - ultimo > timedelta(hours=COOLDOWN_HORAS)

# ─────────────────────────────────────────────────────
# CICLO PRINCIPAL
# ─────────────────────────────────────────────────────

def run():
    log.info("=" * 55)
    log.info(f"🔋 Bateria EV — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    log.info("=" * 55)

    historico = carregar_historico()
    hoje      = datetime.now().strftime("%Y-%m-%d")
    hora_str  = datetime.now().strftime("%d/%m/%Y %H:%M")

    status_frota = fetch_vehicle_status()
    if not status_frota:
        log.error("❌ Sem dados Cartrack — a terminar")
        return

    total = len(status_frota)
    cnt_crit = cnt_aviso = cnt_enviados = 0

    for reg, v in status_frota.items():
        soc         = v.get("soc")
        is_charging = v.get("charging", False)
        em_movimento = v.get("em_movimento", True)

        if soc is None or soc > THRESHOLD_AVISO or soc > 100:
            continue
        if is_charging:
            log.info(f"   {reg} — a carregar ({soc:.0f}%) — ignorado")
            continue
        if not em_movimento:
            log.info(f"   {reg} — parado ({soc:.0f}%) — sem alerta (ignição desligada)")
            continue

        nivel = "CRÍTICO" if soc <= THRESHOLD_CRITICO else "AVISO"
        if soc <= THRESHOLD_CRITICO:
            cnt_crit += 1
        else:
            cnt_aviso += 1

        # ── Buscar motorista do dia na BD ──
        motorista = get_motorista_da_viatura(reg, hoje)
        nome      = motorista["nome"]
        email_mot = motorista["email"]
        telefone  = motorista["telefone"]

        log.warning(f"⚠️  {reg} | {soc:.0f}% | {nivel} | {nome}")

        if not pode_alertar(reg, historico):
            log.info(f"   ↳ cooldown activo — ignorado")
            continue

        if soc <= THRESHOLD_CRITICO:
            # ── WhatsApp ao motorista ──
            wa_msg = (
                f"⚠️ *MOVVI FLEET — Bateria Crítica*\n\n"
                f"Olá {nome},\n\n"
                f"🚗 Viatura: *{reg}*\n"
                f"🔋 Bateria: *{soc:.0f}%* — precisa de carregar!\n"
                f"🕐 Hora: {hora_str}\n\n"
                f"Por favor dirija-se a um posto de carregamento o mais breve possível.\n\n"
                f"_Movvi Fleet Intelligence_"
            )
            if telefone:
                enviar_whatsapp(telefone, wa_msg)
            else:
                log.warning(f"   ↳ sem telefone na BD para {nome}")

            # ── Email ao motorista ──
            assunto = f"⚠️ Bateria Crítica {soc:.0f}% — Viatura {reg}"
            if email_mot:
                enviar_email(email_mot, assunto, html_alerta(nome, reg, soc, hora_str))
            else:
                log.warning(f"   ↳ sem email na BD para {nome}")

            # ── Notificação Cartrack ──
            criar_notificacao_cartrack(reg, soc, nome)

        # ── Cópia sempre ao gestor (crítico + aviso) ──
        assunto_gestor = f"[FROTA] {nivel} bateria {soc:.0f}% — {reg} ({nome})"
        enviar_email(EMAIL_GESTOR, assunto_gestor, html_alerta(nome, reg, soc, hora_str))

        historico[reg] = datetime.now().isoformat()
        cnt_enviados += 1

    guardar_historico(historico)
    log.info(f"✅ {total} viaturas | {cnt_crit} críticas | {cnt_aviso} aviso | {cnt_enviados} alertas enviados")

# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
