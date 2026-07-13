#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI CHARGE — Servidor OCPP 1.6j
===================================
Os postos Wallbox (MOVVI 3 e MOVVI 4) ligam-se aqui via WebSocket.
O Movvi Charge Flask (porta 3001) chama este servidor para:
  · Autorizar um motorista (Authorize)
  · Arrancar o carregamento (RemoteStartTransaction)
  · Parar o carregamento (RemoteStopTransaction)
O posto reporta kWh em tempo real (MeterValues) → débito automático exato.

Instalar:
  /opt/tvde/venv/bin/pip install websockets==12.0
  
Correr (systemd cuida disso):
  source /opt/tvde/.env_charge_systemd
  /opt/tvde/venv/bin/python3 /opt/tvde/movvi-ocpp-server.py

Configurar na Wallbox (portal.wallbox.com → OCPP):
  URL: ws://178.104.20.109:9000/ocpp
  Protocolo: OCPP 1.6j
  Ativar Ligação OCPP WebSocket
"""
import asyncio, json, logging, sqlite3, time, os
from datetime import datetime, timezone
from websockets.server import serve
from websockets.exceptions import ConnectionClosed

logging.basicConfig(level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
    datefmt='%H:%M:%S')
log = logging.getLogger("ocpp")

DB_PATH   = "/opt/tvde/movvi_charge.db"
PRECO_KWH = 0.30
PORT      = 9000

# charger_id (Wallbox) -> {ws, info, transaction_id, kwh, driver_id, driver_nome, license_plate, t_inicio}
CHARGERS = {}
# transaction_id -> charger_id
TRANS = {}
# pending remote commands: charger_id -> {action, payload, future}
PENDING = {}

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
    c.execute("""CREATE TABLE IF NOT EXISTS ocpp_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER, charger_ocpp_id TEXT,
        driver_id INTEGER, driver_nome TEXT, license_plate TEXT,
        kwh_inicio REAL DEFAULT 0, kwh_fim REAL DEFAULT 0,
        inicio TEXT, fim TEXT, estado TEXT DEFAULT 'ativa',
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP)""")
    for col in ["license_plate TEXT", "auto INTEGER DEFAULT 0"]:
        try: c.execute(f"ALTER TABLE debitos_carregamento ADD COLUMN {col}"); c.commit()
        except: pass
    return c

def gravar_debito(driver_id, driver_nome, license_plate, charger_ocpp_id, kwh):
    if kwh < 0.05: return 0
    valor = round(kwh * PRECO_KWH, 2)
    semana = datetime.now().strftime("%G-W%V")
    c = db()
    # nome do posto para o extrato
    ch = CHARGERS.get(charger_ocpp_id, {})
    charger_nome = ch.get("name", charger_ocpp_id)
    c.execute("""INSERT INTO debitos_carregamento
        (driver_id, driver_nome, license_plate, charger_id, charger_nome,
         kwh, preco_kwh, valor, fim, semana, auto)
        VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'),?,1)""",
        (driver_id, driver_nome, license_plate, 0, charger_nome,
         kwh, PRECO_KWH, valor, semana))
    c.commit()
    log.info(f"[DÉBITO] {driver_nome} · {kwh:.2f} kWh = {valor:.2f} € · {semana}")
    return valor

# ─── protocolo OCPP 1.6j ─────────────────────────────────────────────────────
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

async def send_call(ws, action, payload):
    """Envia um CALL ao posto e aguarda a resposta."""
    uid = str(int(time.time() * 1000))
    msg = json.dumps([2, uid, action, payload])
    await ws.send(msg)
    log.info(f"→ {action} {payload}")
    return uid

async def handle_message(charger_id, ws, raw):
    try:
        msg = json.loads(raw)
    except:
        return
    msg_type = msg[0]

    if msg_type == 2:  # CALL do posto
        uid, action, payload = msg[1], msg[2], msg[3]
        log.info(f"← [{charger_id}] {action} {json.dumps(payload)[:120]}")
        ch = CHARGERS.get(charger_id, {})

        if action == "BootNotification":
            CHARGERS[charger_id] = {
                **ch,
                "ws": ws,
                "vendor": payload.get("chargePointVendor",""),
                "model": payload.get("chargePointModel",""),
                "name": f"MOVVI {charger_id[:6]}",
                "connected": True,
            }
            resp = [3, uid, {"status": "Accepted", "currentTime": ts(), "interval": 60}]
            await ws.send(json.dumps(resp))

        elif action == "Heartbeat":
            await ws.send(json.dumps([3, uid, {"currentTime": ts()}]))

        elif action == "StatusNotification":
            status = payload.get("status","")
            CHARGERS.setdefault(charger_id, {})["status"] = status
            log.info(f"[STATUS] {charger_id} → {status}")
            # check-in automático quando cabo é ligado
            if status == "Available":
                # cabo desligado — finalizar sessão ativa se existir
                try:
                    ch = CHARGERS.get(charger_id, {})
                    trans_id = ch.get("transaction_id")
                    if trans_id:
                        kwh_fim = ch.get("kwh_atual", ch.get("kwh_inicio", 0))
                        kwh = kwh_fim - ch.get("kwh_inicio", 0)
                        gravar_debito(
                            ch.get("driver_id", 0), ch.get("driver_nome", "Desconhecido"),
                            ch.get("license_plate", ""), charger_id, kwh)
                        c = db()
                        c.execute("""UPDATE ocpp_sessions SET kwh_fim=?,
                            fim=datetime('now','localtime'), estado='concluida'
                            WHERE transaction_id=?""", (kwh_fim, trans_id))
                        c.commit()
                        for k in ["transaction_id","kwh_inicio","kwh_atual","driver_id","driver_nome","license_plate"]:
                            ch.pop(k, None)
                        TRANS.pop(trans_id, None)
                        log.info(f"[AUTO STOP] {charger_id} cabo desligado → débito {kwh:.2f} kWh")
                except Exception as e:
                    log.error(f"[AUTO STOP] erro: {e}")
            if status == "Preparing":
                try:
                    from datetime import datetime, timedelta
                    agora = datetime.now()
                    janela_ini = (agora - timedelta(minutes=15)).isoformat()
                    janela_fim = (agora + timedelta(minutes=15)).isoformat()
                    # descobrir charger_id Wallbox a partir do OCPP id
                    wb_id = None
                    for cid, cinfo in CHARGERS.items():
                        if cid == charger_id:
                            wb_id = cinfo.get("wallbox_id")
                    # buscar reserva activa para este posto
                    c = db()
                    # buscar reserva activa agora (inicio <= agora <= fim)
                    agora_str = agora.strftime("%Y-%m-%dT%H:%M:%S")
                    row = c.execute("""SELECT id, driver_id, driver_nome, license_plate, charger_id
                        FROM reservas
                        WHERE estado IN ('confirmada','checkin')
                        AND inicio <= ? AND fim >= ?
                        ORDER BY inicio ASC LIMIT 1""",
                        (agora_str, agora_str)).fetchone()
                    if row:
                        rid, did, dnome, plate, cid_num = row
                        ch = CHARGERS.setdefault(charger_id, {})
                        ch["driver_id"] = did
                        ch["driver_nome"] = dnome
                        ch["license_plate"] = plate
                        c.execute("UPDATE reservas SET estado='checkin' WHERE id=?", (rid,))
                        c.commit()
                        log.info(f"[AUTO CHECK-IN] {charger_id} → {dnome} reserva={rid}")
                except Exception as e:
                    log.error(f"[AUTO CHECK-IN] erro: {e}")
            await ws.send(json.dumps([3, uid, {}]))

        elif action == "Authorize":
            # aceita sempre — a autorização real já foi feita no Movvi Charge
            id_tag = payload.get("idTag", "")
            await ws.send(json.dumps([3, uid, {"idTagInfo": {"status": "Accepted"}}]))

        elif action == "StartTransaction":
            trans_id = int(time.time())
            id_tag = payload.get("idTag", "")
            kwh_ini = payload.get("meterStart", 0) / 1000
            TRANS[trans_id] = charger_id
            ch = CHARGERS.setdefault(charger_id, {})
            ch["transaction_id"] = trans_id
            ch["kwh_inicio"] = kwh_ini
            ch["kwh_atual"] = kwh_ini
            ch["t_inicio"] = time.time()
            # gravar sessão OCPP
            c = db()
            c.execute("""INSERT INTO ocpp_sessions
                (transaction_id, charger_ocpp_id, driver_id, driver_nome, license_plate,
                 kwh_inicio, inicio, estado)
                VALUES (?,?,?,?,?,?,datetime('now','localtime'),'ativa')""",
                (trans_id, charger_id,
                 ch.get("driver_id", 0), ch.get("driver_nome", ""), ch.get("license_plate", ""),
                 kwh_ini))
            c.commit()
            await ws.send(json.dumps([3, uid, {"transactionId": trans_id,
                                                "idTagInfo": {"status": "Accepted"}}]))
            log.info(f"[START] trans={trans_id} charger={charger_id}")

        elif action == "StopTransaction":
            trans_id = payload.get("transactionId")
            kwh_fim = payload.get("meterStop", 0) / 1000
            reason = payload.get("reason", "")
            cid = TRANS.pop(trans_id, charger_id)
            ch = CHARGERS.get(cid, {})
            kwh = kwh_fim - ch.get("kwh_inicio", 0)
            # débito automático
            valor = gravar_debito(
                ch.get("driver_id", 0), ch.get("driver_nome", "Desconhecido"),
                ch.get("license_plate", ""), cid, kwh)
            # atualizar sessão OCPP
            c = db()
            c.execute("""UPDATE ocpp_sessions SET kwh_fim=?, fim=datetime('now','localtime'),
                estado='concluida' WHERE transaction_id=?""", (kwh_fim, trans_id))
            c.commit()
            # limpar
            for k in ["transaction_id","kwh_inicio","kwh_atual","driver_id","driver_nome","license_plate"]:
                ch.pop(k, None)
            await ws.send(json.dumps([3, uid, {"idTagInfo": {"status": "Accepted"}}]))
            log.info(f"[STOP] trans={trans_id} kwh={kwh:.2f} valor={valor:.2f}€ motivo={reason}")

        elif action == "MeterValues":
            trans_id = payload.get("transactionId")
            for sv in payload.get("meterValue", []):
                for sv2 in sv.get("sampledValue", []):
                    if sv2.get("measurand","") in ("Energy.Active.Import.Register",""):
                        try:
                            kwh = float(sv2["value"]) / 1000
                            cid = TRANS.get(trans_id, charger_id)
                            if cid in CHARGERS:
                                CHARGERS[cid]["kwh_atual"] = kwh
                        except: pass
            await ws.send(json.dumps([3, uid, {}]))

        else:
            # resposta genérica para ações não implementadas
            await ws.send(json.dumps([3, uid, {}]))

    elif msg_type == 3:  # CALLRESULT — resposta a um nosso CALL
        uid = msg[1]
        log.info(f"← RESULT uid={uid} {json.dumps(msg[2])[:80]}")

    elif msg_type == 4:  # CALLERROR
        log.warning(f"← ERROR {msg}")

# ─── comandos remotos (chamados pelo Flask) ───────────────────────────────────
async def remote_start(charger_id, driver_id, driver_nome, license_plate, id_tag="MOVVI"):
    """Arrancar carregamento remotamente."""
    ch = CHARGERS.get(charger_id)
    if not ch or not ch.get("ws"):
        return {"ok": False, "erro": "posto não ligado ao servidor OCPP"}
    # guardar info do motorista no estado do posto
    ch["driver_id"] = driver_id
    ch["driver_nome"] = driver_nome
    ch["license_plate"] = license_plate
    ws = ch["ws"]
    await send_call(ws, "RemoteStartTransaction",
                    {"connectorId": 1, "idTag": id_tag})
    return {"ok": True}

async def remote_stop(charger_id):
    """Parar carregamento remotamente."""
    ch = CHARGERS.get(charger_id)
    if not ch or not ch.get("ws"):
        return {"ok": False, "erro": "posto não ligado ao servidor OCPP"}
    trans_id = ch.get("transaction_id")
    if not trans_id:
        return {"ok": False, "erro": "sem transação ativa"}
    ws = ch["ws"]
    await send_call(ws, "RemoteStopTransaction", {"transactionId": trans_id})
    return {"ok": True}

def get_status():
    """Estado atual de todos os postos (para o Flask)."""
    return {cid: {
        "connected": c.get("connected", False),
        "status": c.get("status", "Unknown"),
        "transaction_id": c.get("transaction_id"),
        "kwh_atual": c.get("kwh_atual", 0),
        "kwh_inicio": c.get("kwh_inicio", 0),
        "kwh_sessao": c.get("kwh_atual", 0) - c.get("kwh_inicio", 0),
        "driver_nome": c.get("driver_nome"),
        "t_inicio": c.get("t_inicio"),
        "name": c.get("name", cid),
    } for cid, c in CHARGERS.items()}

# ─── servidor WebSocket ───────────────────────────────────────────────────────
async def handler(ws):
    # o path é /ocpp/<charger_id> — ex: /ocpp/MOVVI3
    path = ws.request.path if hasattr(ws, 'request') else getattr(ws, 'path', '/ocpp/unknown')
    charger_id = path.strip("/").split("/")[-1] or "unknown"
    log.info(f"[CONNECT] {charger_id} de {ws.remote_address}")
    CHARGERS.setdefault(charger_id, {})["ws"] = ws
    CHARGERS[charger_id]["connected"] = True
    try:
        async for msg in ws:
            await handle_message(charger_id, ws, msg)
    except ConnectionClosed:
        pass
    finally:
        if charger_id in CHARGERS:
            CHARGERS[charger_id]["connected"] = False
            CHARGERS[charger_id].pop("ws", None)
        log.info(f"[DISCONNECT] {charger_id}")

# ─── API HTTP simples para o Flask comunicar ─────────────────────────────────
# (usa asyncio streams em vez de Flask para evitar conflito de portas)
async def http_api(reader, writer):
    """Mini HTTP server na porta 9001 para o Flask chamar comandos OCPP."""
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=5)
        req = data.decode(errors="ignore")
        lines = req.split("\r\n")
        method_path = lines[0].split(" ") if lines else []
        if len(method_path) < 2:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n"); await writer.drain(); return
        method, path = method_path[0], method_path[1]
        # extrair body JSON
        body = {}
        if "\r\n\r\n" in req:
            try: body = json.loads(req.split("\r\n\r\n", 1)[1])
            except: pass

        result = {"ok": False, "erro": "rota não encontrada"}

        if path == "/ocpp/status":
            result = {"ok": True, "chargers": get_status()}

        elif path == "/ocpp/start" and method == "POST":
            cid = body.get("charger_id", "")
            result = await remote_start(cid,
                body.get("driver_id", 0), body.get("driver_nome", ""),
                body.get("license_plate", ""), body.get("id_tag", "MOVVI"))

        elif path == "/ocpp/stop" and method == "POST":
            result = await remote_stop(body.get("charger_id", ""))

        resp_body = json.dumps(result).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n")
        writer.write(f"Content-Length: {len(resp_body)}\r\n\r\n".encode())
        writer.write(resp_body)
        await writer.drain()
    except Exception as e:
        log.error(f"[HTTP API] {e}")
    finally:
        writer.close()

async def main():
    log.info(f"Movvi OCPP Server a arrancar...")
    ws_server = await serve(handler, "0.0.0.0", PORT,
                            subprotocols=["ocpp1.6"],
                            ping_interval=30, ping_timeout=10)
    http_server = await asyncio.start_server(http_api, "127.0.0.1", 9001)
    log.info(f"WebSocket OCPP: ws://0.0.0.0:{PORT}/ocpp/<charger_id>")
    log.info(f"HTTP API interna: http://127.0.0.1:9001")
    async with ws_server, http_server:
        await asyncio.gather(ws_server.serve_forever(), http_server.serve_forever())

if __name__ == "__main__":
    asyncio.run(main())
