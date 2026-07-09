#!/usr/bin/env python3
"""
MOVVI TVDE Fleet — Mapa da Frota em tempo real (versao final /opt/tvde)
Fontes:
  • Cartrack Fleet API           → posicao, velocidade, ignicao (20s)
  • Bolt Fleet Integration API   → estado motorista por bolt_driver_id (60s)
  • faturacao_uber_live          → online Uber (tempo_online_min a subir)
Grava em tvde_data.db → tabela frota_mapa

Correr:
  cd /opt/tvde && source venv/bin/activate
  nohup python3 mapa_frota.py > logs/mapa_frota.log 2>&1 &
"""

import base64
import logging
import re
import sqlite3
import sys
import time
from datetime import date, datetime

import requests

sys.path.insert(0, "/opt/tvde/pipeline")
from config import (BOLT_API_BASE, BOLT_CLIENT_ID, BOLT_CLIENT_SECRET,
                    BOLT_TOKEN_URL, CARTRACK_BASE_URL, CARTRACK_PASSWORD,
                    CARTRACK_USERNAME, DB_PATH)

# ── correcoes: config.py veio do Windows ──
DB_PATH = "/opt/tvde/tvde_data.db"
BOLT_TOKEN_URL = "https://oidc.bolt.eu/token"
BOLT_API_BASE = "https://node.bolt.eu/fleet-integration-gateway"


INTERVALO_SEG = 20
INTERVALO_BOLT_SEG = 60
INTERVALO_MAPEAMENTO_SEG = 300
UBER_CAPTURA_VALIDADE_MIN = 30   # captura live com mais de 30 min = expirada

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("mapa_frota")


def norm(matricula):
    """'aa-01-bb' → 'AA01BB' para comparar Cartrack vs viaturas."""
    return re.sub(r"[^A-Z0-9]", "", str(matricula or "").upper())


# ── CARTRACK ─────────────────────────────────────────────────────────
class Cartrack:
    def __init__(self):
        cred = base64.b64encode(
            f"{CARTRACK_USERNAME}:{CARTRACK_PASSWORD}".encode()).decode()
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Basic {cred}"})
        self.base = CARTRACK_BASE_URL.rstrip("/")

    def status_veiculos(self):
        r = self.s.get(f"{self.base}/rest/vehicles/status", timeout=15)
        r.raise_for_status()
        itens = r.json().get("data", [])
        out = []
        for v in itens:
            loc = v.get("location") or {}
            lat = loc.get("latitude")
            lng = loc.get("longitude")
            if lat is None or lng is None:
                continue
            ignicao = v.get("ignition")
            out.append({
                "matricula": str(v.get("registration") or "").strip().upper(),
                "lat": float(lat),
                "lng": float(lng),
                "vel": int(float(v.get("speed") or 0)),
                "ignicao": bool(ignicao),
                "event_ts": str(v.get("event_ts") or ""),
            })
        return out


# ── BOLT — estado por bolt_driver_id ─────────────────────────────────
class Bolt:
    ONLINE = {"waiting_orders", "has_order", "online", "busy", "active"}

    def __init__(self):
        self.token = None
        self.token_exp = 0
        self.company_id = None

    def _auth(self):
        if self.token and time.time() < self.token_exp - 60:
            return
        r = requests.post(BOLT_TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": BOLT_CLIENT_ID,
            "client_secret": BOLT_CLIENT_SECRET,
            "scope": "fleet-integration:api",
        }, timeout=15)
        r.raise_for_status()
        j = r.json()
        self.token = j["access_token"]
        self.token_exp = time.time() + int(j.get("expires_in", 600))

    def _post(self, path, payload):
        self._auth()
        r = requests.post(f"{BOLT_API_BASE.rstrip('/')}{path}", json=payload,
                          headers={"Authorization": f"Bearer {self.token}"},
                          timeout=15)
        r.raise_for_status()
        return r.json().get("data", {})

    def _cid(self):
        if self.company_id:
            return self.company_id
        d = self._post("/fleetIntegration/v1/getCompanies", {})
        ids = d.get("company_ids") or [c.get("id")
                                       for c in d.get("companies", [])]
        self.company_id = ids[0]
        return self.company_id

    def driver_ids_online(self):
        """Devolve {bolt_driver_id(str): True} dos motoristas online."""
        d = self._post("/fleetIntegration/v1/getDrivers",
                       {"company_id": self._cid(), "offset": 0, "limit": 500})
        online = {}
        for drv in d.get("drivers", []):
            if str(drv.get("state", "")).lower() in self.ONLINE:
                did = drv.get("id") or drv.get("driver_id")
                if did is not None:
                    online[str(did)] = True
        return online


# ── UBER — online se tempo_online_min subiu na ultima captura ────────
def uber_ids_online(con):
    """Compara as 2 ultimas capturas de hoje por motorista.
    Se a ultima e recente E (tempo_online subiu OU corridas subiram)
    → motorista ativo na Uber. Devolve {motorista_id: True}."""
    hoje = date.today().isoformat()
    rows = con.execute("""
        SELECT motorista_id, capturado_em, tempo_online_min, num_corridas
        FROM faturacao_uber_live
        WHERE data = ? AND motorista_id IS NOT NULL
        ORDER BY motorista_id, capturado_em DESC
    """, (hoje,)).fetchall()

    agora = datetime.now()
    por_mot = {}
    for mid, cap, online_min, corridas in rows:
        por_mot.setdefault(mid, []).append((cap, online_min or 0, corridas or 0))

    ativos = {}
    for mid, caps in por_mot.items():
        if len(caps) < 2:
            continue
        (cap1, t1, c1), (cap2, t2, c2) = caps[0], caps[1]
        try:
            idade_min = (agora - datetime.fromisoformat(cap1)).total_seconds() / 60
        except ValueError:
            continue
        if idade_min <= UBER_CAPTURA_VALIDADE_MIN and (t1 > t2 or c1 > c2):
            ativos[mid] = True
    return ativos


# ── MAPEAMENTO matricula → motorista (atribuicoes de hoje) ───────────
def mapa_viaturas(con):
    """{matricula_norm: {matricula, nome, motorista_id, bolt_driver_id}}"""
    hoje = date.today().isoformat()
    q = """
        SELECT vt.matricula, m.nome, m.id, COALESCE(m.bolt_driver_id,'')
        FROM atribuicoes a
        JOIN motoristas m ON m.id = a.motorista_id
        JOIN viaturas  vt ON vt.id = a.viatura_id
        WHERE a.data = ?
    """
    rows = con.execute(q, (hoje,)).fetchall()
    if not rows:   # sem atribuicao hoje → ultima conhecida por viatura
        rows = con.execute("""
            SELECT vt.matricula, m.nome, m.id, COALESCE(m.bolt_driver_id,'')
            FROM atribuicoes a
            JOIN motoristas m ON m.id = a.motorista_id
            JOIN viaturas  vt ON vt.id = a.viatura_id
            WHERE a.id IN (SELECT MAX(id) FROM atribuicoes GROUP BY viatura_id)
        """).fetchall()
    return {norm(r[0]): {"matricula": r[0], "nome": r[1],
                         "motorista_id": r[2], "bolt_id": str(r[3])}
            for r in rows}


# ── BASE DE DADOS ────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS frota_mapa (
            matricula TEXT PRIMARY KEY, motorista TEXT,
            lat REAL, lng REAL, velocidade INTEGER, ignicao INTEGER,
            app TEXT, movendo INTEGER, parado_desde TEXT, atualizado_em TEXT)
    """)
    con.commit()
    con.close()


def app_ativa_hoje(con):
    from datetime import date, datetime, timedelta
    hoje = date.today().isoformat()
    ontem = (date.today() - timedelta(days=1)).isoformat()
    bolt_ids, uber_ids = set(), set()

    for mid, in con.execute("""
        SELECT DISTINCT motorista_id FROM faturacao_bolt
        WHERE data IN (?,?) AND motorista_id IS NOT NULL
    """, (hoje, ontem)):
        bolt_ids.add(mid)

    limite = (datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    uber_hoje = con.execute(
        "SELECT COUNT(*) FROM faturacao_uber_live WHERE data=? AND capturado_em>=?",
        (hoje, limite)).fetchone()[0]
    data_uber = hoje if uber_hoje > 0 else ontem
    params = (data_uber, limite) if uber_hoje > 0 else (data_uber,)
    extra = "AND capturado_em>=?" if uber_hoje > 0 else ""
    for mid, in con.execute(f"""
        SELECT DISTINCT motorista_id FROM faturacao_uber_live
        WHERE data=? AND (num_corridas>0 OR tempo_online_min>0)
          AND motorista_id IS NOT NULL {extra}
    """, params):
        uber_ids.add(mid)

    ativos = {}
    for mid in bolt_ids | uber_ids:
        if mid in bolt_ids and mid in uber_ids:
            ativos[mid] = 'ambos'
        elif mid in uber_ids:
            ativos[mid] = 'uber'
        else:
            ativos[mid] = 'bolt'
    return ativos

def gravar(posicoes, bolt_on, mapa_v):
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    uber_on = uber_ids_online(con)
    agora = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    for p in posicoes:
        chave = norm(p["matricula"])
        info = mapa_v.get(chave, {})
        matricula = info.get("matricula", p["matricula"])
        movendo = 1 if p["vel"] > 3 else 0

        # usar fim da ultima viagem da cartrack_viagens
        parado_desde_real = None
        if not movendo:
            row_v = con.execute(
                "SELECT fim FROM cartrack_viagens "
                "WHERE matricula=? ORDER BY fim DESC LIMIT 1",
                (matricula,)).fetchone()
            if row_v and row_v[0]:
                try:
                    from dateutil import parser as _dp
                    import datetime as _dt
                    parsed = _dp.parse(str(row_v[0]).strip())
                    if parsed.tzinfo:
                        utc = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                    else:
                        utc = parsed - _dt.timedelta(hours=1)
                    parado_desde_real = utc.strftime("%Y-%m-%dT%H:%M:%S")
                except: pass

        mid = info.get("motorista_id")
        _apps = app_ativa_hoje(con)
        app = _apps.get(mid, "offline")

        row = con.execute(
            "SELECT movendo, parado_desde FROM frota_mapa WHERE matricula=?",
            (matricula,)).fetchone()
        if movendo:
            parado_desde = None
        elif parado_desde_real:
            parado_desde = parado_desde_real  # timestamp real da Cartrack
        elif row and row[0] == 0 and row[1]:
            parado_desde = row[1]
        else:
            parado_desde = agora

        con.execute("""
            INSERT INTO frota_mapa VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(matricula) DO UPDATE SET
              motorista=excluded.motorista, lat=excluded.lat,
              lng=excluded.lng, velocidade=excluded.velocidade,
              ignicao=excluded.ignicao, app=excluded.app,
              movendo=excluded.movendo, parado_desde=excluded.parado_desde,
              atualizado_em=excluded.atualizado_em
        """, (matricula, info.get("nome"), p["lat"], p["lng"], p["vel"],
              int(p["ignicao"]), app, movendo, parado_desde, agora))
    con.commit()
    con.close()


# ── LOOP ─────────────────────────────────────────────────────────────
def main():
    init_db()
    ct, bolt = Cartrack(), Bolt()
    bolt_on, mapa_v = {}, {}
    t_bolt = t_map = 0

    log.info("Mapa da frota iniciado — DB: %s", DB_PATH)
    while True:
        try:
            agora = time.time()
            if agora - t_bolt > INTERVALO_BOLT_SEG:
                try:
                    bolt_on = bolt.driver_ids_online()
                    log.info("Bolt: %d motoristas online", len(bolt_on))
                except Exception as e:
                    # NAO limpar: falha de auth != motoristas offline
                    log.warning("Bolt indisponivel (mantem estado): %s", e)
                t_bolt = agora

            if agora - t_map > INTERVALO_MAPEAMENTO_SEG:
                con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
                mapa_v = mapa_viaturas(con)
                con.close()
                log.info("Mapeamento: %d viaturas com motorista", len(mapa_v))
                t_map = agora

            pos = ct.status_veiculos()
            gravar(pos, bolt_on, mapa_v)
            log.info("%d viaturas atualizadas", len(pos))
        except Exception as e:
            log.error("Ciclo falhou: %s", e)
            if "no such table" in str(e):
                init_db()
                log.info("Tabela recriada")
        time.sleep(INTERVALO_SEG)


if __name__ == "__main__":
    main()
