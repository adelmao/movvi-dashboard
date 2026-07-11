#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI CHARGE v3 — Wallbox + Movvi + TVDE Fleet
===============================================
Novidades v3:
  · Segurança: motorista só termina a SUA sessão; gestão termina qualquer uma
  · Débito automático: quando Wallbox deteta fim de sessão, debita sozinho
  · Reservas: calendário de slots (30/30min), check-in, auto-cancelamento
  · Nomes corretos: "Movvi" em vez de "GesTVDE"
"""
import os, time, sqlite3, base64, secrets, threading
import requests
from flask import Flask, request, jsonify, g

WALLBOX_EMAIL = os.environ.get("WALLBOX_EMAIL")
WALLBOX_PASS  = os.environ.get("WALLBOX_PASS")
MOVVI_EMAIL   = os.environ.get("MOVVI_EMAIL")
MOVVI_PASS    = os.environ.get("MOVVI_PASS")
API_KEY       = os.environ.get("CHARGE_API_KEY", "")

PRECO_KWH     = 0.30
CASA_ID       = 451775
DB_PATH       = "/opt/tvde/tvde_data.db"
APP_HTML      = "/opt/tvde/movvi-charge-app.html"
CHECKIN_TTL   = 30 * 60   # 30 min para fazer check-in ou reserva cancela
SESSAO_TTL    = 12 * 3600

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
WB_H = {"User-Agent": UA, "Accept": "application/json"}
MV_H = {"User-Agent": UA, "Accept": "application/json"}
MOVVI_API = "https://movvi.com.pt/api"

app = Flask(__name__)

# arrancar monitor de alertas em background
try:
    from movvi_charge_alertas import iniciar_monitor_alertas
    iniciar_monitor_alertas()
except Exception as e:
    print(f'[ALERTAS] nao carregado: {e}')
SESSOES = {}   # token -> {driver_id, name, license_plate, model, exp}

# ─── auth ────────────────────────────────────────────────────────────────────
@app.before_request
def _auth():
    if request.method == "OPTIONS" or request.path in ("/", "/index.html"):
        return None
    pub = ["/api/driver-login", "/api/slots", "/api/chargers/public"] + \
        [r for r in [request.path] if r.startswith("/cancelar/")]
    if request.path in pub:
        return None
    if request.headers.get("X-Api-Key") == API_KEY and API_KEY:
        g.perfil = "admin"; g.driver = None; return None
    tok = request.headers.get("X-Driver-Token", "")
    _limpar_sessoes()
    if tok in SESSOES:
        g.perfil = "driver"; g.driver = SESSOES[tok]
        ok = request.path in ("/api/chargers", "/api/my") \
             or request.path.startswith("/api/debitos/fechar/") \
             or request.path.startswith("/api/reservas")
        if ok: return None
    return jsonify({"erro": "nao autorizado"}), 401

def _limpar_sessoes():
    now = time.time()
    for t in [t for t, s in list(SESSOES.items()) if s["exp"] < now]:
        del SESSOES[t]

# ─── base de dados ────────────────────────────────────────────────────────────
def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS debitos_carregamento(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER, driver_nome TEXT, license_plate TEXT,
        charger_id INTEGER, charger_nome TEXT,
        kwh REAL, preco_kwh REAL, valor REAL,
        inicio TEXT, fim TEXT, semana TEXT,
        auto INTEGER DEFAULT 0,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS reservas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER, driver_nome TEXT, license_plate TEXT,
        charger_id INTEGER, charger_nome TEXT,
        inicio TEXT, fim TEXT, duracao_min INTEGER,
        estado TEXT DEFAULT 'confirmada',
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # migrações seguras
    c.execute("""CREATE TABLE IF NOT EXISTS cancel_tokens(
        token TEXT PRIMARY KEY,
        reserva_id INTEGER,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        usado INTEGER DEFAULT 0)""")
    for col in ["license_plate TEXT", "auto INTEGER DEFAULT 0"]:
        try: c.execute(f"ALTER TABLE debitos_carregamento ADD COLUMN {col}"); c.commit()
        except: pass
    return c

# ─── Wallbox ─────────────────────────────────────────────────────────────────
_wb = {"jwt": None, "exp": 0}

def wb_token():
    if _wb["jwt"] and time.time() < _wb["exp"] - 3600:
        return _wb["jwt"]
    basic = base64.b64encode(f"{WALLBOX_EMAIL}:{WALLBOX_PASS}".encode()).decode()
    r = requests.get("https://api.wall-box.com/auth/token/user",
                     headers={**WB_H, "Authorization": f"Basic {basic}"}, timeout=20)
    if r.status_code != 200:
        raise Exception(f"wallbox token {r.status_code}: {r.text[:120]}")
    d = r.json(); _wb["jwt"], _wb["exp"] = d["jwt"], d["ttl"]
    return _wb["jwt"]

def wb_chargers():
    r = requests.get("https://api.wall-box.com/v3/chargers/groups",
                     headers={**WB_H, "Authorization": f"Bearer {wb_token()}"}, timeout=20)
    if r.status_code != 200:
        raise Exception(f"wallbox groups {r.status_code}: {r.text[:120]}")
    out = []
    for grp in r.json()["result"]["groups"]:
        for c in grp["chargers"]:
            if c["id"] == CASA_ID: continue
            out.append({"id": c["id"], "name": c["name"],
                        "status": c["status"], "charging": c["status"] == 194,
                        "kwNow": c["chargingPower"], "sessionKwh": c["addedEnergy"],
                        "valorSessao": round(c["addedEnergy"] * PRECO_KWH, 2),
                        "online": bool(c.get("connectionType"))})
    return out

# ─── Movvi (GesTVDE) ─────────────────────────────────────────────────────────
_mv = {"tok": None}

def mv_login(email, password):
    r = requests.post(f"{MOVVI_API}/login",
                      json={"email": email, "password": password},
                      headers=MV_H, timeout=20)
    return r.json() if r.status_code == 200 else None

def mv_token():
    if _mv["tok"]: return _mv["tok"]
    d = mv_login(MOVVI_EMAIL, MOVVI_PASS)
    if not d: raise Exception("login Movvi admin falhou")
    _mv["tok"] = d["access_token"]; return _mv["tok"]

def mv_get(path, params=None, token=None):
    tok = token or mv_token()
    r = requests.get(f"{MOVVI_API}{path}", params=params or {},
                     headers={**MV_H, "Authorization": f"Bearer {tok}"}, timeout=25)
    if r.status_code == 401 and not token:
        _mv["tok"] = None
        r = requests.get(f"{MOVVI_API}{path}", params=params or {},
                         headers={**MV_H, "Authorization": f"Bearer {mv_token()}"}, timeout=25)
    if r.status_code != 200:
        raise Exception(f"movvi {path} {r.status_code}: {r.text[:120]}")
    return r.json()

_ativos_cache = {"data": [], "exp": 0}
def ativos():
    if time.time() < _ativos_cache["exp"]: return _ativos_cache["data"]
    hoje = time.strftime("%Y-%m-%d")
    d = mv_get("/v1/vehicle-usages", {"active_on": hoje, "per_page": 100})
    out, vistos = [], set()
    for u in d.get("items", []):
        drv, veh = u.get("driver") or {}, u.get("vehicle") or {}
        if not drv.get("id") or drv["id"] in vistos: continue
        vistos.add(drv["id"])
        out.append({"driver_id": drv["id"], "name": drv.get("name", ""),
                    "vehicle_id": veh.get("id"),
                    "license_plate": veh.get("license_plate", ""),
                    "model": veh.get("model") or veh.get("brand") or ""})
    out.sort(key=lambda x: x["name"])
    _ativos_cache["data"], _ativos_cache["exp"] = out, time.time() + 600
    return out

# ─── sessões ativas por posto (para segurança e débito auto) ─────────────────
# charger_id -> {driver_id, name, license_plate, kwh_inicio, t_inicio}
SESSOES_ATIVAS = {}

# ─── débito automático ───────────────────────────────────────────────────────
def _gravar_debito(driver_id, nome, matricula, charger_id, charger_nome, kwh, auto=False):
    if kwh < 0.05: return
    valor = round(kwh * PRECO_KWH, 2)
    semana = time.strftime("%G-W%V")
    c = db()
    c.execute("""INSERT INTO debitos_carregamento
        (driver_id, driver_nome, license_plate, charger_id, charger_nome,
         kwh, preco_kwh, valor, fim, semana, auto)
        VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'),?,?)""",
        (driver_id, nome, matricula, charger_id, charger_nome,
         kwh, PRECO_KWH, valor, semana, 1 if auto else 0))
    c.commit()
    return valor

def _monitor():
    """Corre em background — deteta fim de sessão e debita automaticamente."""
    estados_ant = {}
    while True:
        try:
            chargers = wb_chargers()
            for c in chargers:
                cid, charging = c["id"], c["charging"]
                era_charging = estados_ant.get(cid, False)
                # posto parou de carregar
                if era_charging and not charging and cid in SESSOES_ATIVAS:
                    sess = SESSOES_ATIVAS.pop(cid)
                    kwh = c["sessionKwh"]
                    _gravar_debito(sess["driver_id"], sess["name"],
                                   sess["license_plate"], cid, c["name"], kwh, auto=True)
                    print(f"[AUTO] débito {sess['name']} · {kwh:.2f} kWh · {round(kwh*PRECO_KWH,2)} €")
                estados_ant[cid] = charging
        except Exception as e:
            print(f"[monitor] {e}")
        time.sleep(60)

threading.Thread(target=_monitor, daemon=True).start()

# ─── endpoints ───────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return open(APP_HTML, encoding="utf-8").read()

@app.post("/api/driver-login")
def driver_login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify({"erro": "email e password obrigatórios"}), 400
    d = mv_login(email, password)
    if not d: return jsonify({"erro": "Credenciais inválidas no Movvi"}), 401
    info = None
    try:
        hoje = time.strftime("%Y-%m-%d")
        u = mv_get("/v1/vehicle-usages", {"active_on": hoje, "per_page": 5}, token=d["access_token"])
        if u.get("viewer", {}).get("is_driver") and u.get("items"):
            drv, veh = u["items"][0].get("driver") or {}, u["items"][0].get("vehicle") or {}
            info = {"driver_id": drv.get("id"), "name": drv.get("name", ""),
                    "license_plate": veh.get("license_plate", ""), "model": veh.get("model") or ""}
    except: pass
    if not info:
        try:
            todos = mv_get("/v1/drivers", {"per_page": 300}).get("data", [])
            meu = next((x for x in todos if
                (x.get("email") or "").strip().lower() == email or
                ((x.get("user") or {}).get("email") or "").strip().lower() == email), None)
            if meu:
                atv = next((a for a in ativos() if a["driver_id"] == meu["id"]), None)
                info = {"driver_id": meu["id"], "name": meu["name"],
                        "license_plate": (atv or {}).get("license_plate", ""),
                        "model": (atv or {}).get("model", "")}
        except: pass
    if not info or not info.get("driver_id"):
        return jsonify({"erro": "Login válido, mas não encontrei o teu registo no Movvi. Fala com a gestão."}), 403
    if not any(a["driver_id"] == info["driver_id"] for a in ativos()):
        return jsonify({"erro": "Não tens viatura ativa atribuída hoje. Fala com a gestão."}), 403
    tok = secrets.token_hex(24)
    SESSOES[tok] = {**info, "exp": time.time() + SESSAO_TTL}
    return jsonify({"token": tok, **info})

@app.get("/api/chargers")
def api_chargers():
    try:
        chars = wb_chargers()
        # enriquecer com sessão ativa (quem está a carregar)
        for c in chars:
            sess = SESSOES_ATIVAS.get(c["id"])
            c["sessao_driver_id"] = sess["driver_id"] if sess else None
            c["sessao_nome"] = sess["name"] if sess else None
        return jsonify(chars)
    except Exception as e:
        return jsonify({"erro": str(e)}), 502

@app.get("/api/chargers/public")
def api_chargers_public():
    """Postos sem dados sensíveis — para vista pública de disponibilidade."""
    try:
        chars = wb_chargers()
        for c in chars:
            sess = SESSOES_ATIVAS.get(c["id"])
            c["ocupado_por"] = sess["name"] if sess else None
        return jsonify([{"id": c["id"], "name": c["name"], "charging": c["charging"],
                         "online": c["online"], "kwNow": c["kwNow"],
                         "ocupado_por": c["ocupado_por"]} for c in chars])
    except Exception as e:
        return jsonify({"erro": str(e)}), 502

@app.get("/api/active")
def api_active():
    try: return jsonify(ativos())
    except Exception as e: return jsonify({"erro": str(e)}), 502

@app.get("/api/my")
def api_my():
    drv = g.driver
    c = db()
    cur = c.execute("""SELECT charger_nome, kwh, valor, semana, fim, license_plate, auto
                       FROM debitos_carregamento WHERE driver_id=?
                       ORDER BY id DESC LIMIT 30""", (drv["driver_id"],))
    deb = [{"charger": r[0], "kwh": r[1], "valor": r[2], "semana": r[3],
            "fim": r[4], "license_plate": r[5], "auto": bool(r[6])} for r in cur.fetchall()]
    return jsonify({"driver": {k: drv[k] for k in ("driver_id","name","license_plate","model")},
                    "debitos": deb})

@app.get("/api/debitos")
def api_debitos():
    c = db(); cur = c.execute("SELECT * FROM debitos_carregamento ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall(); cols = [x[0] for x in cur.description]
    return jsonify([dict(zip(cols, r)) for r in rows])

@app.get("/api/debitos/export")
def api_export():
    semana = request.args.get("semana", time.strftime("%G-W%V"))
    c = db(); cur = c.execute("""SELECT driver_id,driver_nome,license_plate,charger_nome,kwh,valor,fim
        FROM debitos_carregamento WHERE semana=? ORDER BY fim""", (semana,))
    lin = ["driver_id;nome;matricula;descricao;kwh;valor;data"]
    for did,nome,mat,posto,kwh,valor,fim in cur.fetchall():
        lin.append(f"{did};{nome};{mat or ''};Carregamento {posto};{kwh:.2f};{valor:.2f};{fim}")
    return "\n".join(lin), 200, {"Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename=debitos_carregamento_{semana}.csv"}

@app.post("/api/debitos/fechar/<int:charger_id>")
def fechar(charger_id):
    if g.perfil == "driver":
        # segurança: só pode fechar se for A SUA sessão
        sess = SESSOES_ATIVAS.get(charger_id)
        if not sess or sess["driver_id"] != g.driver["driver_id"]:
            return jsonify({"erro": "Só podes terminar a tua própria sessão."}), 403
        did, nome, mat = g.driver["driver_id"], g.driver["name"], g.driver["license_plate"]
    else:
        did = request.args.get("driver_id", type=int)
        a = next((x for x in ativos() if x["driver_id"] == did), None)
        nome = (a or {}).get("name", f"driver {did}"); mat = (a or {}).get("license_plate", "")
    try:
        ch = next((x for x in wb_chargers() if x["id"] == charger_id), None)
    except Exception as e:
        return jsonify({"erro": str(e)}), 502
    if not ch: return jsonify({"erro": "posto não encontrado"}), 404
    kwh = ch["sessionKwh"]
    if kwh < 0.05: return jsonify({"erro": "sessão sem consumo para debitar"}), 400
    SESSOES_ATIVAS.pop(charger_id, None)
    valor = _gravar_debito(did, nome, mat, charger_id, ch["name"], kwh)
    return jsonify({"ok": True, "driver": nome, "license_plate": mat,
                    "kwh": kwh, "valor": valor, "semana": time.strftime("%G-W%V")})

# ─── reservas ────────────────────────────────────────────────────────────────

@app.get("/api/reservas/todas")
def api_todas_reservas():
    """Gestão: todas as reservas (qualquer estado)."""
    c = db()
    cur = c.execute("""SELECT id, driver_id, driver_nome, license_plate,
                       charger_id, charger_nome, inicio, fim, duracao_min, estado
                       FROM reservas ORDER BY inicio ASC LIMIT 200""")
    cols = [x[0] for x in cur.description]
    return jsonify([dict(zip(cols, r)) for r in cur.fetchall()])

@app.post("/api/reservas/<int:rid>/noshow")
def noshow(rid):
    c = db()
    c.execute("UPDATE reservas SET estado='no_show' WHERE id=?", (rid,))
    c.commit()
    return jsonify({"ok": True})

@app.get("/api/slots")
def api_slots():
    """Slots do dia — público. ?data=2026-07-11"""
    data = request.args.get("data", time.strftime("%Y-%m-%d"))
    c = db()
    cur = c.execute("""SELECT id, driver_nome, license_plate, charger_id, charger_nome,
                       inicio, fim, duracao_min, estado FROM reservas
                       WHERE date(inicio)=? AND estado NOT IN ('cancelada','no_show')
                       ORDER BY inicio""", (data,))
    cols = [x[0] for x in cur.description]
    return jsonify([dict(zip(cols, r)) for r in cur.fetchall()])

@app.get("/api/reservas")
def api_minhas_reservas():
    drv = g.driver
    c = db()
    cur = c.execute("""SELECT id, charger_nome, inicio, fim, duracao_min, estado
                       FROM reservas WHERE driver_id=? AND estado='confirmada'
                       ORDER BY inicio DESC LIMIT 10""", (drv["driver_id"],))
    cols = [x[0] for x in cur.description]
    return jsonify([dict(zip(cols, r)) for r in cur.fetchall()])

@app.post("/api/reservas")
def criar_reserva():
    drv = g.driver
    body = request.get_json(silent=True) or {}
    charger_id = body.get("charger_id")
    inicio = body.get("inicio")    # "2026-07-11T14:00"
    duracao = int(body.get("duracao_min", 60))
    if not charger_id or not inicio:
        return jsonify({"erro": "charger_id e inicio obrigatórios"}), 400
    # calcular fim
    from datetime import datetime, timedelta
    ini = datetime.fromisoformat(inicio)
    fim = ini + timedelta(minutes=duracao)
    # verificar conflito
    c = db()
    conflito = c.execute("""SELECT id FROM reservas
        WHERE charger_id=? AND estado NOT IN ('cancelada','no_show','concluida')
        AND inicio < ? AND fim > ?""",
        (charger_id, fim.isoformat(), ini.isoformat())).fetchone()
    if conflito:
        return jsonify({"erro": "Posto já reservado nesse horário."}), 409
    # descobrir nome do posto
    try:
        ch = next((x for x in wb_chargers() if x["id"] == charger_id), None)
        charger_nome = ch["name"] if ch else f"Posto {charger_id}"
    except:
        charger_nome = f"Posto {charger_id}"
    c.execute("""INSERT INTO reservas
        (driver_id, driver_nome, license_plate, charger_id, charger_nome,
         inicio, fim, duracao_min, estado)
        VALUES (?,?,?,?,?,?,?,?,'confirmada')""",
        (drv["driver_id"], drv["name"], drv["license_plate"],
         charger_id, charger_nome, ini.isoformat(), fim.isoformat(), duracao))
    c.commit()
    return jsonify({"ok": True, "charger": charger_nome,
                    "inicio": ini.strftime("%H:%M"), "fim": fim.strftime("%H:%M"),
                    "duracao_min": duracao})

@app.delete("/api/reservas/<int:rid>")
def cancelar_reserva(rid):
    drv = g.driver
    c = db()
    if g.perfil == "admin":
        c.execute("UPDATE reservas SET estado='cancelada' WHERE id=?", (rid,))
    else:
        c.execute("UPDATE reservas SET estado='cancelada' WHERE id=? AND driver_id=?",
                  (rid, drv["driver_id"]))
    c.commit()
    return jsonify({"ok": True})

@app.post("/api/reservas/<int:rid>/checkin")
def checkin(rid):
    drv = g.driver
    c = db()
    r = c.execute("SELECT * FROM reservas WHERE id=? AND driver_id=? AND estado='confirmada'",
                  (rid, drv["driver_id"])).fetchone()
    if not r: return jsonify({"erro": "Reserva não encontrada ou já usada."}), 404
    cols = [x[0] for x in c.description]
    res = dict(zip(cols, r))
    # registar sessão ativa
    SESSOES_ATIVAS[res["charger_id"]] = {
        "driver_id": drv["driver_id"], "name": drv["name"],
        "license_plate": drv["license_plate"], "t_inicio": time.time()
    }
    c.execute("UPDATE reservas SET estado='checkin' WHERE id=?", (rid,))
    c.commit()
    return jsonify({"ok": True, "charger": res["charger_nome"], "msg": "Check-in feito! Liga o cabo."})


# ─── cancelamento por link (sem login) ───────────────────────────────────────
def gerar_cancel_token(reserva_id):
    """Gera um token único de cancelamento para incluir no WhatsApp/email."""
    tok = secrets.token_urlsafe(20)
    c = db()
    c.execute("INSERT OR REPLACE INTO cancel_tokens (token, reserva_id) VALUES (?,?)",
              (tok, reserva_id))
    c.commit()
    return tok

@app.get("/cancelar/<token>")
def cancelar_por_link(token):
    """Motorista clica no link → reserva cancelada, página de confirmação."""
    c = db()
    row = c.execute("SELECT reserva_id, usado FROM cancel_tokens WHERE token=?", (token,)).fetchone()
    if not row:
        return "<html><body style='font-family:Inter,sans-serif;text-align:center;padding:40px'><h2>❌ Link inválido ou expirado.</h2></body></html>", 404
    reserva_id, usado = row
    if usado:
        return """<html><body style='font-family:Inter,sans-serif;text-align:center;padding:40px'>
        <h2>✅ Reserva já cancelada.</h2>
        <p style='color:#666'>Esta reserva já foi cancelada anteriormente.</p>
        <a href='https://charge.movvi.com.pt' style='color:#146B3A'>Abrir Movvi Charge</a>
        </body></html>"""
    # cancelar a reserva
    res = c.execute("SELECT driver_nome, charger_nome, inicio FROM reservas WHERE id=?", (reserva_id,)).fetchone()
    c.execute("UPDATE reservas SET estado='cancelada' WHERE id=?", (reserva_id,))
    c.execute("UPDATE cancel_tokens SET usado=1 WHERE token=?", (token,))
    c.commit()
    nome = res[0] if res else "Motorista"
    posto = (res[1] or "").split(" SN")[0] if res else "Posto"
    hora = res[2][11:16] if res else ""
    return f"""<html>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Movvi Charge — Cancelamento</title>
<link href='https://fonts.googleapis.com/css2?family=Archivo:wght@800&family=Inter:wght@400;600&display=swap' rel='stylesheet'>
<style>body{{font-family:Inter,sans-serif;background:#F4F6F5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border-radius:16px;padding:32px 24px;max-width:360px;width:90%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.1)}}
.logo{{font-family:Archivo,sans-serif;font-weight:800;font-size:28px;letter-spacing:-.03em;margin-bottom:20px}}
.v1{{color:#146B3A}}.v2{{color:#E63329}}
.v2::before{{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;background:#101312;vertical-align:super;margin-right:1px}}
h2{{font-size:20px;font-weight:700;margin-bottom:8px;color:#101312}}
p{{color:#5C6663;font-size:14px;line-height:1.6;margin-bottom:20px}}
.info{{background:#F4F6F5;border-radius:10px;padding:12px;font-size:13px;margin-bottom:20px;text-align:left}}
.info b{{display:block;font-weight:600;margin-bottom:4px}}
a{{display:inline-block;background:#146B3A;color:#fff;padding:12px 24px;border-radius:10px;text-decoration:none;font-weight:600;font-size:14px}}
.foot{{margin-top:24px;font-size:11px;color:#9AA6A2}}
</style></head>
<body><div class='card'>
<div class='logo'>MO<span class='v1'>V</span><span class='v2'>V</span>I <span style='font-size:10px;letter-spacing:.3em;font-family:monospace'>TVDE</span></div>
<h2>✅ Reserva cancelada</h2>
<p>A tua reserva foi cancelada com sucesso.</p>
<div class='info'>
  <b>Detalhes</b>
  Motorista: {nome}<br>
  Posto: {posto}<br>
  Hora: {hora}
</div>
<p>O posto ficou livre para outros motoristas.</p>
<a href='https://charge.movvi.com.pt'>Abrir Movvi Charge</a>
<div class='foot'>Movvi TVDE · A frota mais moderna de Portugal<br>adelmo.pt</div>
</div></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3001)
