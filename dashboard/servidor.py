"""
TVDE Fleet - Servidor Web com Login
Serve o dashboard em http://localhost:5000
"""

import json, os, sqlite3, glob, csv, re, requests, hashlib, secrets, time, base64
from http.server import HTTPServer, HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from repositories.database import get_db

# ── CONFIG ──────────────────────────────────────────────────
DB_PATH        = "/opt/tvde/tvde_data.db"
REPORTS_FOLDER = "/opt/tvde/relatorios"
UBER_CSV_FOLDER= "/opt/tvde/uber_exports"
PIPELINE_DIR   = "/opt/tvde/pipeline"
PORT           = 5000
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
COMPANY_UUID   = "5f72a5f2-74e2-4b4e-9667-168c0bb59841"

# ── UTILIZADORES ────────────────────────────────────────────
# Senha em hash SHA256 — para gerar: python -c "import hashlib; print(hashlib.sha256('ASUASENHA'.encode()).hexdigest())"
USERS = {
    "adelmo":  {"hash": hashlib.sha256("adelmo2024".encode()).hexdigest(), "nome": "Adelmo"},
    "denis":  {"hash": hashlib.sha256("Movvi123".encode()).hexdigest(), "nome": "Denis"},
    "karla":  {"hash": "a962d4efb9c9e00036e3fa326fc70d18067b0217b403a778e12fbd81d79198e6", "nome": "Karla"},
    "marina": {"hash": "a962d4efb9c9e00036e3fa326fc70d18067b0217b403a778e12fbd81d79198e6", "nome": "Marina"},
    "andre":  {"hash": "a962d4efb9c9e00036e3fa326fc70d18067b0217b403a778e12fbd81d79198e6", "nome": "Andre"},
    "daniel": {"hash": hashlib.sha256("Movvi123".encode()).hexdigest(), "nome": "Daniel"},
    "victor": {"hash": hashlib.sha256("Movvi123".encode()).hexdigest(), "nome": "Victor"},
}

# Sessões activas {token: {user, expires}}
SESSIONS = {}
SESSION_TTL = 8 * 3600  # 8 horas

MOTOR_SESSIONS = {}

def create_motor_session(motorista_id, email):
    token = secrets.token_hex(32)
    MOTOR_SESSIONS[token] = {
        "motorista_id": motorista_id,
        "email":        email,
        "expires":      time.time() + SESSION_TTL
    }
    return token

def get_motor_session(cookie_header):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("tvde_motor="):
            token = part[len("tvde_motor="):]
            sess  = MOTOR_SESSIONS.get(token)
            if sess and sess["expires"] > time.time():
                return sess
    return None


def create_session(username):
    token = secrets.token_hex(32)
    SESSIONS[token] = {
        "user":    username,
        "nome":    USERS[username]["nome"],
        "expires": time.time() + SESSION_TTL
    }
    return token


def get_session(cookie_header):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("tvde_session="):
            token = part[len("tvde_session="):]
            sess  = SESSIONS.get(token)
            if sess and sess["expires"] > time.time():
                return sess
    return None


def clean_sessions():
    now = time.time()
    for k in list(SESSIONS.keys()):
        if SESSIONS[k]["expires"] < now:
            del SESSIONS[k]


# ── LOGIN PAGE ───────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movvi TVDE — Login</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 16px}
  .card{background:#1e293b;border-radius:16px;padding:2rem 1.5rem;width:92vw;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.5);text-align:center}
  .logo-img{width:150px;margin:0 auto 1.2rem;display:block}
  h1{color:#f1f5f9;font-size:1.5rem;margin-bottom:.4rem;font-weight:700}
  .subtitle{color:#94a3b8;font-size:.82rem;margin-bottom:.3rem}
  .dev{color:#475569;font-size:.65rem;margin-bottom:1.8rem}
  label{display:block;font-size:.72rem;font-weight:600;color:#94a3b8;
        margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.05em;text-align:left}
  input{width:100%;padding:.7rem 1rem;background:#0f172a;border:1.5px solid #334155;
        border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none;
        transition:border .2s;margin-bottom:1rem}
  input:focus{border-color:#3b82f6}
  button{width:100%;padding:.75rem;background:#3b82f6;color:white;border:none;
         border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer;transition:background .2s}
  button:hover{background:#2563eb}
  .err{color:#f87171;font-size:.8rem;margin-top:.5rem;text-align:center}
</style>
</head>
<body>
<div class="card">
  <img class="logo-img" src="/logomovvi.jpg"
       alt="Movvi" onerror="this.style.display='none'">
  <h1>Movvi TVDE</h1>
  <p class="subtitle">Acesso restrito — introduza as suas credenciais</p>
  <p class="dev">app desenvolvida por Adelmo Filho</p>
  <form method="POST" action="/login">
    <label>Utilizador</label>
    <input type="text" name="username" autocomplete="username" required autofocus>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
    {error}
  </form>
</div>
</body>
</html>"""


# ── DB HELPER ───────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── KPI QUERIES ─────────────────────────────────────────────
def get_kpis(date_str=None, from_date=None, to_date=None):
    conn = get_db()
    c    = conn.cursor()

    if from_date and to_date:
        kpis_raw = c.execute("""
            SELECT motorista_id,
                MAX(nome_motorista) AS nome_motorista,
                MAX(matricula)      AS matricula,
                SUM(fat_bolt)       AS fat_bolt,
                SUM(fat_uber)       AS fat_uber,
                SUM(faturacao_liquida) AS faturacao_liquida,
                SUM(km_total)       AS km_total,
                SUM(num_corridas)   AS num_corridas,
                ROUND(SUM(faturacao_liquida)/NULLIF(SUM(km_total),0), 3) AS receita_por_km
            FROM kpis_diarios
            WHERE data BETWEEN ? AND ?
            GROUP BY motorista_id
            ORDER BY receita_por_km DESC
        """, (from_date, to_date)).fetchall()
        date_str = to_date

        atrib_sem_kpi = c.execute("""
            SELECT DISTINCT m.id, m.nome, v.matricula
            FROM atribuicoes a
            JOIN motoristas m ON a.motorista_id = m.id
            JOIN viaturas v ON a.viatura_id = v.id
            WHERE a.data BETWEEN ? AND ?
            AND m.id NOT IN (
                SELECT DISTINCT motorista_id FROM kpis_diarios
                WHERE data BETWEEN ? AND ?
            )
        """, (from_date, to_date, from_date, to_date)).fetchall()
    else:
        if not date_str:
            row = c.execute("SELECT MAX(data) FROM kpis_diarios").fetchone()
            date_str = row[0] if row and row[0] else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        from_date = to_date = date_str

        kpis_raw = c.execute("""
            SELECT * FROM kpis_diarios WHERE data = ? ORDER BY receita_por_km DESC
        """, (date_str,)).fetchall()

        atrib_sem_kpi = c.execute("""
            SELECT DISTINCT m.id, m.nome, v.matricula
            FROM atribuicoes a
            JOIN motoristas m ON a.motorista_id = m.id
            JOIN viaturas v ON a.viatura_id = v.id
            WHERE a.data = ?
            AND m.id NOT IN (SELECT DISTINCT motorista_id FROM kpis_diarios WHERE data = ?)
        """, (date_str, date_str)).fetchall()

    historico = c.execute("""
        SELECT data,
               SUM(faturacao_liquida) AS fat_total,
               SUM(km_total)          AS km_total,
               SUM(num_corridas)      AS corridas,
               ROUND(SUM(faturacao_liquida)/NULLIF(SUM(km_total),0), 3) AS receita_km
        FROM kpis_diarios
        WHERE data >= date('now', '-30 days')
        GROUP BY data ORDER BY data ASC
    """).fetchall()

    # Hora do ultimo registo Bolt e Uber
    gerado_em = datetime.now().isoformat()
    last_bolt = None
    try:
        row_b = conn.execute(
            'SELECT MAX(importado_em) FROM faturacao_bolt WHERE data=?', (to_date,)
        ).fetchone()[0]
        if row_b:
            last_bolt = row_b[:19]
    except: pass
    last_uber = None
    try:
        row_u = conn.execute(
            'SELECT MAX(capturado_em) FROM faturacao_uber_live WHERE data=?', (to_date,)
        ).fetchone()[0]
        if row_u:
            last_uber = row_u[:19]
    except: pass
    last_cartrack = None
    try:
        row_c = conn.execute(
            'SELECT MAX(importado_em) FROM km_viaturas WHERE data=?', (to_date,)
        ).fetchone()[0]
        if row_c:
            last_cartrack = row_c[:19]
    except: pass
    last_prio = None
    try:
        row_p = conn.execute(
            """SELECT MAX(data_transacao || ' ' || hora_transacao)
               FROM prio_transacoes WHERE data BETWEEN ? AND ?""",
            (from_date, to_date)
        ).fetchone()[0]
        if row_p:
            last_prio = row_p[:19]
    except: pass

    kpis_list = [dict(r) for r in kpis_raw]
    for r in atrib_sem_kpi:
        kpis_list.append({
            "motorista_id": r[0], "nome_motorista": r[1], "matricula": r[2],
            "fat_bolt": 0, "fat_uber": 0, "faturacao_liquida": 0,
            "km_total": 0, "num_corridas": 0, "receita_por_km": 0
        })

    conn.close()
    return {
        "gerado_em":    gerado_em,
        "data":         date_str,
        "from_date":    from_date,
        "to_date":      to_date,
        "kpis_dia":     kpis_list,
        "historico_30d":[dict(r) for r in historico],
        "last_bolt":    last_bolt,
        "last_uber":    last_uber,
        "last_cartrack": last_cartrack,
        "last_prio_import": last_prio,
    }


def get_datas_disponiveis():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT data FROM kpis_diarios ORDER BY data DESC LIMIT 60").fetchall()
    conn.close()
    return {"datas": [r[0] for r in rows]}


def get_prio(from_date=None, to_date=None):
    conn = get_db()
    c    = conn.cursor()
    if not from_date:
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prio_transacoes'").fetchone()
    if not tbl:
        conn.close()
        return {"viaturas": [], "from_date": from_date, "to_date": to_date, "total_kwh": 0, "total_valor": 0}
    rows = c.execute("""
        SELECT matricula, COUNT(*) n_carregamentos,
               ROUND(SUM(kwh),2) total_kwh, ROUND(SUM(valor),2) total_valor
        FROM prio_transacoes WHERE data BETWEEN ? AND ? AND matricula != ''
        GROUP BY matricula ORDER BY total_kwh DESC
    """, (from_date, to_date)).fetchall()
    viaturas = []
    for r in rows:
        mat = r[0]
        km  = c.execute("""
            SELECT ROUND(SUM(km_total),1) FROM km_viaturas
            WHERE matricula=? AND data BETWEEN ? AND ?
        """, (mat, from_date, to_date)).fetchone()
        km_total = float(km[0] or 0) if km else 0
        kwh_km = round(r[2]/km_total, 3) if km_total > 0 else None
        eur_km = round(r[3]/km_total, 3) if km_total > 0 else None

        # Por motorista usando hora da transacção
        # Quando há sobreposição (viatura permanente + nova atribuição),
        # o custo vai para o motorista com start_hora mais recente <= data da transacção
        por_motorista = c.execute("""
            SELECT m.nome AS nome_motorista,
                   COUNT(DISTINCT pt.id) AS n_carregamentos,
                   ROUND(SUM(DISTINCT pt.kwh), 2) AS total_kwh,
                   ROUND(SUM(DISTINCT pt.valor), 2) AS total_valor
            FROM prio_transacoes pt
            JOIN atribuicoes a ON (
                a.viatura_id = (SELECT id FROM viaturas WHERE matricula = pt.matricula)
                AND a.data = pt.data
                AND (
                    (a.start_hora IS NULL OR a.start_hora = '')
                    OR (substr(a.start_hora, 1, 10) <= pt.data AND substr(a.end_hora, 1, 10) >= pt.data)
                )
            )
            JOIN motoristas m ON m.id = a.motorista_id
            WHERE pt.matricula = ? AND pt.data BETWEEN ? AND ?
            GROUP BY m.id
            ORDER BY total_valor DESC
        """, (mat, from_date, to_date)).fetchall()

        viaturas.append({"matricula": mat, "n_carregamentos": r[1],
                         "total_kwh": r[2], "total_valor": r[3],
                         "km_total": km_total, "kwh_km": kwh_km, "eur_km": eur_km,
                         "por_motorista": [dict(pm) for pm in por_motorista]})
    totais = c.execute("""
        SELECT ROUND(SUM(kwh),2), ROUND(SUM(valor),2)
        FROM prio_transacoes WHERE data BETWEEN ? AND ? AND matricula != ''
    """, (from_date, to_date)).fetchone()
    prio_ultima = c.execute("SELECT MAX(data_transacao) FROM prio_transacoes WHERE data BETWEEN ? AND ?", (from_date, to_date)).fetchone()[0] or ""
    conn.close()
    return {"from_date": from_date, "to_date": to_date, "viaturas": viaturas,
            "total_kwh": totais[0] or 0, "total_valor": totais[1] or 0, "prio_ultima_actualizacao": prio_ultima}


def _uber_csv_total(date_str):
    """
    Procura APENAS o consolidado oficial: curr-next_0805.
    Parciais nao sao usados para verificacao (incompletos).
    """
    curr = date_str.replace("-", "")
    nxt  = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")

    # Consolidado oficial: curr-next com _0805 (08:05 do dia seguinte)
    consolidados = glob.glob(os.path.join(UBER_CSV_FOLDER, f"{curr}-{nxt}-*_0805.csv"))
    # Tambem aceitar _0815 (ligeira variacao de hora)
    consolidados += glob.glob(os.path.join(UBER_CSV_FOLDER, f"{curr}-{nxt}-*_0815.csv"))

    if not consolidados:
        return None, 0, 0

    f = sorted(consolidados)[-1]
    uuid_totais = {}
    try:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
            for row in reader:
                uuid = row.get("UUID do motorista", "").strip()
                if not uuid or uuid == COMPANY_UUID: continue
                if row.get("Pago a si:Saldo da viagem:Pagamentos:Transferido para uma conta bancaria", "").strip(): continue
                val = float((row.get("Pago a si", "") or "0").replace(",", ".") or 0)
                if uuid not in uuid_totais:
                    uuid_totais[uuid] = val
    except Exception as e:
        return None, 0, 0

    total = round(sum(uuid_totais.values()), 2)
    count = len(uuid_totais)
    return [f], total, count


def get_duplo_check(date_str):
    result = {"data": date_str, "uber": {}, "bolt": {}}
    files, csv_total, csv_count = _uber_csv_total(date_str)
    conn = get_db()
    bd = conn.execute(
        "SELECT COUNT(*), ROUND(SUM(faturacao_liquida),2) FROM faturacao_uber WHERE data=?",
        (date_str,)
    ).fetchone()
    bd_count = bd[0] or 0; bd_total = bd[1] or 0
    diff = round(bd_total - csv_total, 2) if files else None
    result["uber"] = {"bd_total": bd_total, "bd_count": bd_count,
                      "csv_total": csv_total, "csv_count": csv_count,
                      "diferenca": diff, "ok": diff is not None and abs(diff) < 2.0,
                      "sem_ficheiro": files is None}
    bd_b = conn.execute(
        "SELECT COUNT(*), ROUND(SUM(faturacao_liquida),2) FROM faturacao_bolt WHERE data=?",
        (date_str,)
    ).fetchone()
    conn.close()
    bolt_bd_count = bd_b[0] or 0; bolt_bd_total = bd_b[1] or 0
    jwt = None
    try:
        with open(os.path.join(PIPELINE_DIR, "config.py"), encoding="utf-8") as f:
            m = re.search(r'BOLT_JWT_TOKEN\s*=\s*"([^"]+)"', f.read())
            jwt = m.group(1) if m else None
    except: pass
    bolt_api_total = None; bolt_api_count = 0; bolt_api_error = None
    if jwt:
        try:
            r = requests.post(
                "https://fleetownerportal.live.boltsvc.net/fleetOwnerPortal/driverEarnings/getTable",
                params={"language":"pt-pt","version":"FO.3.1991","company_id":19252,"user_id":17957,"brand":"bolt"},
                json={"start_date":date_str,"end_date":date_str,"limit":200,"offset":0},
                headers={"Authorization":f"Bearer {jwt}","Content-Type":"application/json",
                         "Origin":"https://fleets.bolt.eu","Referer":"https://fleets.bolt.eu/"},
                timeout=10
            )
            data = r.json()
            if data.get("code") == 0:
                # API Bolt devolve columns com cells, não rows
                columns = data.get("data", {}).get("columns", [])
                net_col = next((c for c in columns if c.get("key") == "net_earnings"), None)
                if net_col:
                    cells = net_col.get("cells", [])
                    bolt_api_total = round(sum(float(v or 0) for v in cells), 2)
                    bolt_api_count = len(cells)
                else:
                    # fallback para rows se existir
                    rows = data.get("data", {}).get("rows", [])
                    bolt_api_total = round(sum(float(row.get("earnings_after_fees", 0) or 0) for row in rows), 2)
                    bolt_api_count = len(rows)
            else: bolt_api_error = data.get("message", "Erro API")
        except Exception as e: bolt_api_error = str(e)
    else: bolt_api_error = "Sem JWT"
    bolt_diff = round(bolt_bd_total - bolt_api_total, 2) if bolt_api_total is not None else None
    result["bolt"] = {"bd_total": bolt_bd_total, "bd_count": bolt_bd_count,
                      "api_total": bolt_api_total, "api_count": bolt_api_count,
                      "diferenca": bolt_diff,
                      "ok": bolt_diff is not None and abs(bolt_diff) < 2.0,
                      "erro": bolt_api_error}
    return result


def get_uber_check_historico():
    """
    Compara dados dentro da BD (nao precisa ficheiros CSV no servidor):
      - faturacao_uber = CSV oficial carregado na BD
      - kpis_diarios.fat_uber = valor real usado (CSV ou Live)

    Logica:
      - CSV na BD existente e proximo de kpis → CSV foi fonte → ✅
      - CSV na BD muito menor que kpis → Live complementou → ⚠ inconclusivo
      - Sem CSV na BD → so Live → ⚠ sem CSV
    """
    hoje = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    datas = [r[0] for r in conn.execute(
        "SELECT DISTINCT data FROM kpis_diarios ORDER BY data DESC LIMIT 30"
    ).fetchall()]

    resultados = []
    for date_str in datas:
        # KPIs: valor real usado (pode ser CSV + Live ou so Live)
        kpi = conn.execute(
            "SELECT COUNT(*), ROUND(SUM(fat_uber),2) FROM kpis_diarios WHERE data=? AND fat_uber > 0",
            (date_str,)
        ).fetchone()
        kpi_count = kpi[0] or 0
        kpi_total = float(kpi[1] or 0)

        # CSV na BD: dados do CSV oficial carregado pelo connector_uber
        csv_bd = conn.execute(
            "SELECT COUNT(DISTINCT uber_uuid), ROUND(SUM(faturacao_liquida),2) FROM faturacao_uber WHERE data=?",
            (date_str,)
        ).fetchone()
        csv_count = csv_bd[0] or 0
        csv_total = float(csv_bd[1] or 0)

        from datetime import datetime as _dt
        d_week = _dt.strptime(date_str, "%Y-%m-%d").weekday()
        transferencia = kpi_total < 0 and d_week == 0

        # Hoje: Live e primario, CSV parcial esperado
        if date_str == hoje:
            diff = None
            ok = None
            sem_ficheiro = True
        elif csv_total > 0:
            diff = round(kpi_total - csv_total, 2)
            # Tolerancia de 5%: Live pode complementar com motoristas em falta
            tolerancia = max(kpi_total * 0.05, 2.0)
            if abs(diff) <= tolerancia:
                ok = True   # CSV foi fonte principal, diferenca aceitavel
            elif diff > tolerancia:
                ok = None   # kpis > CSV: Live complementou — inconclusivo
            else:
                ok = False  # kpis < CSV: erro real
            sem_ficheiro = False
        else:
            # Sem CSV na BD para este dia
            diff = None
            ok = None
            sem_ficheiro = True

        resultados.append({
            "data": date_str,
            "bd_total": kpi_total,
            "bd_count": kpi_count,
            "transferencia": transferencia,
            "csv_total": csv_total if csv_total > 0 else 0,
            "csv_count": csv_count,
            "diferenca": diff,
            "ok": ok,
            "sem_ficheiro": sem_ficheiro
        })

    conn.close()
    return {"dias": resultados}




def get_kpis_motorista(motorista_id, from_date=None, to_date=None):
    conn = get_db()
    c    = conn.cursor()

    if not to_date:
        row = c.execute("SELECT MAX(data) FROM kpis_diarios").fetchone()
        to_date = row[0] if row and row[0] else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if not from_date:
        from_date = (datetime.strptime(to_date, "%Y-%m-%d") - timedelta(days=29)).strftime("%Y-%m-%d")

    totais = c.execute("""
        SELECT
            ROUND(SUM(faturacao_liquida), 2) AS fat_total,
            ROUND(SUM(fat_bolt), 2)          AS fat_bolt,
            ROUND(SUM(fat_uber), 2)          AS fat_uber,
            ROUND(SUM(km_total), 1)          AS km_total,
            SUM(num_corridas)                AS corridas,
            ROUND(SUM(faturacao_liquida)/NULLIF(SUM(km_total),0), 3) AS receita_km
        FROM kpis_diarios
        WHERE motorista_id = ? AND data BETWEEN ? AND ?
    """, (motorista_id, from_date, to_date)).fetchone()

    historico = c.execute("""
        SELECT data,
               ROUND(faturacao_liquida, 2)  AS fat,
               ROUND(fat_bolt, 2)           AS bolt,
               ROUND(fat_uber, 2)           AS uber,
               ROUND(km_total, 1)           AS km,
               num_corridas                 AS corridas,
               ROUND(receita_por_km, 3)     AS receita_km
        FROM kpis_diarios
        WHERE motorista_id = ? AND data BETWEEN ? AND ?
        ORDER BY data ASC
    """, (motorista_id, from_date, to_date)).fetchall()

    info = c.execute("""
        SELECT m.nome, MAX(v.matricula) AS matricula
        FROM motoristas m
        LEFT JOIN atribuicoes a ON a.motorista_id = m.id AND a.data BETWEEN ? AND ?
        LEFT JOIN viaturas v ON v.id = a.viatura_id
        WHERE m.id = ?
        GROUP BY m.id
    """, (from_date, to_date, motorista_id)).fetchone()

    conn.close()
    return {
        "motorista_id": motorista_id,
        "nome":         info["nome"] if info else "",
        "matricula":    info["matricula"] if info else "",
        "from_date":    from_date,
        "to_date":      to_date,
        "totais": {
            "fat_total":  float(totais[0] or 0),
            "fat_bolt":   float(totais[1] or 0),
            "fat_uber":   float(totais[2] or 0),
            "km_total":   float(totais[3] or 0),
            "corridas":   int(totais[4] or 0),
            "receita_km": float(totais[5] or 0),
        },
        "historico": [dict(r) for r in historico],
    }
# ── VIA VERDE ────────────────────────────────────────────────
def get_viaverde(from_date=None, to_date=None):
    conn = get_db()
    c = conn.cursor()

    # Verifica tabelas
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='via_verde'").fetchone()
    if not tbl:
        conn.close()
        return {"total": 0, "por_viatura": [], "por_motorista": [], "semanas": [], "ultima_importacao": None}

    tem_diario = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='via_verde_diario'").fetchone()

    if from_date and to_date:
        por_viatura = c.execute("""
            SELECT vvd.matricula,
                   SUM(vvd.num_transacoes) AS num_transacoes,
                   ROUND(SUM(vvd.total_euros), 2) AS total_euros
            FROM via_verde_diario vvd
            WHERE vvd.data BETWEEN ? AND ?
            GROUP BY vvd.matricula
            ORDER BY total_euros DESC
        """, (from_date, to_date)).fetchall() if tem_diario else []

        total = c.execute("""
            SELECT ROUND(SUM(total_euros), 2)
            FROM via_verde WHERE data_inicio <= ? AND data_fim >= ?
        """, (to_date, from_date)).fetchone()[0] or 0

        # Por motorista — usa hora das transacções para calcular correctamente
        tem_tx = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='via_verde_transacoes'").fetchone()
        if tem_tx:
            por_motorista = c.execute("""
                SELECT
                    m.nome                              AS nome_motorista,
                    v.matricula                         AS matricula,
                    COUNT(*)                            AS num_transacoes,
                    ROUND(SUM(sub.valor), 2)            AS total_euros
                FROM (
                    SELECT
                        vvt.id,
                        vvt.matricula,
                        vvt.valor,
                        vvt.data,
                        (
                            SELECT a.motorista_id
                            FROM atribuicoes a
                            JOIN viaturas v2 ON v2.id = a.viatura_id
                            WHERE v2.matricula = vvt.matricula
                              AND a.data = vvt.data
                              AND (
                                  (a.start_hora IS NULL OR a.start_hora = '')
                                  OR (
                                      substr(a.start_hora, 1, 10) <= vvt.data
                                      AND substr(a.end_hora, 1, 10) >= vvt.data
                                  )
                              )
                            ORDER BY a.start_hora DESC
                            LIMIT 1
                        ) AS motorista_id
                    FROM via_verde_transacoes vvt
                    WHERE vvt.data BETWEEN ? AND ?
                ) sub
                JOIN motoristas m  ON m.id = sub.motorista_id
                JOIN viaturas v    ON v.matricula = sub.matricula
                WHERE sub.motorista_id IS NOT NULL
                GROUP BY m.id, v.matricula
                ORDER BY total_euros DESC
            """, (from_date, to_date)).fetchall()
        else:
            por_motorista = c.execute("""
                SELECT
                    m.nome                              AS nome_motorista,
                    v.matricula                         AS matricula,
                    SUM(vvd.num_transacoes)             AS num_transacoes,
                    ROUND(SUM(vvd.total_euros), 2)      AS total_euros
                FROM via_verde_diario vvd
                JOIN atribuicoes a   ON a.data = vvd.data
                JOIN viaturas v      ON v.id = a.viatura_id AND v.matricula = vvd.matricula
                JOIN motoristas m    ON m.id = a.motorista_id
                WHERE vvd.data BETWEEN ? AND ?
                GROUP BY m.id, v.matricula
                ORDER BY total_euros DESC
            """, (from_date, to_date)).fetchall() if tem_diario else []

    else:
        ultima_semana = c.execute(
            "SELECT semana FROM via_verde ORDER BY data_fim DESC LIMIT 1"
        ).fetchone()
        if not ultima_semana:
            conn.close()
            return {"total": 0, "por_viatura": [], "por_motorista": [], "semanas": [], "ultima_importacao": None}

        semana = ultima_semana[0]
        datas_sem = c.execute(
            "SELECT MIN(data_inicio), MAX(data_fim) FROM via_verde WHERE semana=?", (semana,)
        ).fetchone()
        d_from, d_to = datas_sem[0], datas_sem[1]

        por_viatura = c.execute("""
            SELECT matricula, SUM(num_transacoes) AS num_transacoes,
                   ROUND(SUM(total_euros),2) AS total_euros
            FROM via_verde_diario WHERE data BETWEEN ? AND ?
            GROUP BY matricula ORDER BY total_euros DESC
        """, (d_from, d_to)).fetchall() if tem_diario else c.execute("""
            SELECT matricula, num_transacoes, ROUND(total_euros,2) AS total_euros
            FROM via_verde WHERE semana=? ORDER BY total_euros DESC
        """, (semana,)).fetchall()

        total = c.execute(
            "SELECT ROUND(SUM(total_euros),2) FROM via_verde WHERE semana=?", (semana,)
        ).fetchone()[0] or 0

        tem_tx = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='via_verde_transacoes'").fetchone()
        if tem_tx:
            por_motorista = c.execute("""
                SELECT
                    m.nome                              AS nome_motorista,
                    v.matricula                         AS matricula,
                    COUNT(vvt.id)                       AS num_transacoes,
                    ROUND(SUM(vvt.valor), 2)            AS total_euros
                FROM via_verde_transacoes vvt
                JOIN atribuicoes a ON a.data = vvt.data
                JOIN viaturas v    ON v.id = a.viatura_id AND v.matricula = vvt.matricula
                JOIN motoristas m  ON m.id = a.motorista_id
                WHERE vvt.data BETWEEN ? AND ?
                  -- O motorista estava activo nesse dia: start_hora_date <= data E end_hora_date >= data
                  AND substr(a.start_hora, 1, 10) <= vvt.data
                  AND substr(a.end_hora,   1, 10) >= vvt.data
                  -- Se dois motoristas cobrem o mesmo dia, usa hora de saida para desempatar:
                  -- a portagem pertence ao motorista cuja end_hora (hora) >= hora da portagem
                  -- OU cuja end_hora é no futuro (data > data portagem)
                  AND (
                    substr(a.end_hora, 1, 10) > vvt.data
                    OR time(substr(a.end_hora, 12)) >= time(vvt.hora)
                  )
                GROUP BY m.id, v.matricula
                ORDER BY total_euros DESC
            """, (d_from, d_to)).fetchall()
        else:
            por_motorista = c.execute("""
                SELECT
                    m.nome                              AS nome_motorista,
                    v.matricula                         AS matricula,
                    SUM(vvd.num_transacoes)             AS num_transacoes,
                    ROUND(SUM(vvd.total_euros), 2)      AS total_euros
                FROM via_verde_diario vvd
                JOIN atribuicoes a   ON a.data = vvd.data
                JOIN viaturas v      ON v.id = a.viatura_id AND v.matricula = vvd.matricula
                JOIN motoristas m    ON m.id = a.motorista_id
                WHERE vvd.data BETWEEN ? AND ?
                GROUP BY m.id, v.matricula
                ORDER BY total_euros DESC
            """, (d_from, d_to)).fetchall() if tem_diario else []

    # Histórico semanal
    semanas = c.execute("""
        SELECT semana, data_inicio, data_fim,
               ROUND(SUM(total_euros),2) AS total,
               COUNT(DISTINCT matricula) AS viaturas
        FROM via_verde GROUP BY semana
        ORDER BY data_fim DESC LIMIT 8
    """).fetchall()

    ultima_importacao = c.execute("SELECT MAX(importado_em) FROM via_verde").fetchone()[0]

    conn.close()
    return {
        "total": total or 0,
        "por_viatura":   [dict(r) for r in por_viatura],
        "por_motorista": [dict(r) for r in por_motorista],
        "semanas":       [dict(r) for r in semanas],
        "ultima_importacao": ultima_importacao
    }


MOTOR_LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movvi - Area do Motorista</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#0f172a;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:0 16px}
  .card{background:#1e293b;border-radius:16px;padding:2rem 1.5rem;width:92%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.5);text-align:center}
  .logo-img{width:130px;margin:0 auto 1.2rem;display:block}
  h1{color:#f1f5f9;font-size:1.4rem;margin-bottom:.3rem;font-weight:700}
  .sub{color:#64748b;font-size:.8rem;margin-bottom:1.8rem}
  label{display:block;font-size:.7rem;font-weight:600;color:#94a3b8;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.05em;text-align:left}
  input{width:100%;padding:.7rem 1rem;background:#0f172a;border:1.5px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none;transition:border .2s;margin-bottom:1rem}
  input:focus{border-color:#22d3ee}
  button{width:100%;padding:.75rem;background:#22d3ee;color:#0f172a;border:none;border-radius:8px;font-size:.9rem;font-weight:700;cursor:pointer}
  .err{color:#f87171;font-size:.8rem;margin-top:.5rem}
  .hint{color:#475569;font-size:.72rem;margin-top:1.2rem;line-height:1.5}
</style>
</head>
<body>
<div class="card">
  <img class="logo-img" src="/logomovvi.jpg" alt="Movvi" onerror="this.style.display='none'">
  <h1>Area do Motorista</h1>
  <p class="sub">Acede aos teus resultados</p>
  <form method="POST" action="/motorista/login">
    <label>Email</label>
    <input type="email" name="email" autocomplete="email" required autofocus>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
    {error}
  </form>
  <p class="hint">Usa o email registado no Movvi.<br>No primeiro acesso define a tua password.</p>
</div>
</body>
</html>"""

MOTOR_SET_PASS_HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movvi - Definir Password</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#0f172a;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:0 16px}
  .card{background:#1e293b;border-radius:16px;padding:2rem 1.5rem;width:92%;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,.5);text-align:center}
  h1{color:#f1f5f9;font-size:1.3rem;margin-bottom:.4rem;font-weight:700}
  .sub{color:#64748b;font-size:.8rem;margin-bottom:1.8rem}
  label{display:block;font-size:.7rem;font-weight:600;color:#94a3b8;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.05em;text-align:left}
  input{width:100%;padding:.7rem 1rem;background:#0f172a;border:1.5px solid #334155;border-radius:8px;color:#f1f5f9;font-size:.9rem;outline:none;transition:border .2s;margin-bottom:1rem}
  input:focus{border-color:#22d3ee}
  button{width:100%;padding:.75rem;background:#22d3ee;color:#0f172a;border:none;border-radius:8px;font-size:.9rem;font-weight:700;cursor:pointer}
  .err{color:#f87171;font-size:.8rem;margin-top:.5rem}
</style>
</head>
<body>
<div class="card">
  <h1>Primeiro Acesso</h1>
  <p class="sub">Define a tua password para a area do motorista</p>
  <form method="POST" action="/motorista/set-password">
    <input type="hidden" name="email" value="{email}">
    <label>Nova Password</label>
    <input type="password" name="password" minlength="6" required autofocus>
    <label>Confirmar Password</label>
    <input type="password" name="password2" minlength="6" required>
    <button type="submit">Guardar e Entrar</button>
    {error}
  </form>
</div>
</body>
</html>"""


def get_prio_motorista(motorista_id, from_date, to_date):
    conn = get_db()
    c = conn.cursor()
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prio_transacoes'").fetchone()
    if not tbl:
        conn.close()
        return {"kwh": 0, "valor": 0, "n_carregamentos": 0, "kwh_km": None, "eur_km": None}
    # Custos PRIO - usar subquery para evitar duplicados quando ha multiplas atribuicoes
    row = c.execute("""
        SELECT COUNT(*), ROUND(SUM(kwh),2), ROUND(SUM(valor),2)
        FROM (
            SELECT DISTINCT pt.id, pt.kwh, pt.valor
            FROM prio_transacoes pt
            JOIN viaturas v ON v.matricula = pt.matricula
            JOIN atribuicoes a ON (
                a.viatura_id = v.id
                AND a.data = pt.data
                AND a.motorista_id = ?
            )
            WHERE pt.data BETWEEN ? AND ?
              AND (
                a.start_hora IS NULL
                OR (pt.data || ' ' || pt.hora_transacao) >= a.start_hora
              )
              AND (
                a.end_hora IS NULL
                OR (pt.data || ' ' || pt.hora_transacao) <= a.end_hora
              )
        )
    """, (motorista_id, from_date, to_date)).fetchone()
    n_car = row[0] or 0
    kwh   = float(row[1] or 0)
    valor = float(row[2] or 0)
    # Km do motorista no periodo (de kpis_diarios)
    km_row = c.execute("""
        SELECT ROUND(SUM(km_total),1) FROM kpis_diarios
        WHERE motorista_id=? AND data BETWEEN ? AND ?
    """, (motorista_id, from_date, to_date)).fetchone()
    km_total = float(km_row[0] or 0) if km_row else 0
    conn.close()
    return {
        "n_carregamentos": n_car,
        "kwh":    kwh,
        "valor":  valor,
        "kwh_km": round(kwh/km_total, 3) if km_total > 0 else None,
        "eur_km": round(valor/km_total, 3) if km_total > 0 else None,
    }


def get_ranking_motorista(motorista_id, from_date, to_date):
    conn = get_db()
    rows = conn.execute("""
        SELECT motorista_id,
               ROUND(SUM(faturacao_liquida)/NULLIF(SUM(km_total),0), 3) AS ekm
        FROM kpis_diarios
        WHERE data BETWEEN ? AND ? AND km_total > 0
        GROUP BY motorista_id
        ORDER BY ekm DESC
    """, (from_date, to_date)).fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    total = len(ids)
    posicao = ids.index(motorista_id) + 1 if motorista_id in ids else None
    return {"posicao": posicao, "total": total}


def get_prio_motorista_detalhe(motorista_id, from_date, to_date):
    conn = get_db()
    c = conn.cursor()
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='prio_transacoes'").fetchone()
    if not tbl:
        conn.close()
        return {"transacoes": []}
    matriculas = [r[0] for r in c.execute("""
        SELECT DISTINCT v.matricula FROM atribuicoes a
        JOIN viaturas v ON v.id = a.viatura_id
        WHERE a.motorista_id = ? AND a.data BETWEEN ? AND ?
    """, (motorista_id, from_date, to_date)).fetchall()]
    if not matriculas:
        conn.close()
        return {"transacoes": []}
    # Buscar carregamentos filtrando por hora do turno do motorista
    rows = c.execute("""
        SELECT DISTINCT pt.data, pt.hora_transacao, pt.matricula,
                        pt.station_name, pt.kwh, pt.valor
        FROM prio_transacoes pt
        JOIN viaturas v ON v.matricula = pt.matricula
        JOIN atribuicoes a ON (
            a.viatura_id = v.id
            AND a.data = pt.data
            AND a.motorista_id = ?
        )
        WHERE pt.data BETWEEN ? AND ?
          AND (
            a.start_hora IS NULL
            OR (pt.data || ' ' || pt.hora_transacao) >= a.start_hora
          )
          AND (
            a.end_hora IS NULL
            OR (pt.data || ' ' || pt.hora_transacao) <= a.end_hora
          )
        ORDER BY pt.data DESC, pt.hora_transacao DESC
    """, (motorista_id, from_date, to_date)).fetchall()
    conn.close()
    return {"transacoes": [dict(r) for r in rows]}

def get_comportamento_frota(from_date, to_date):
    """Devolve scores de comportamento de todos os motoristas para o dashboard principal."""
    conn = get_db()
    c = conn.cursor()

    all_drivers = c.execute("""
        SELECT
            a.motorista_id,
            m.nome,
            SUM(kv.km_total)                       AS km,
            SUM(kv.travagens_bruscas)              AS trav,
            SUM(kv.aceleracoes_bruscas)            AS acel,
            SUM(kv.curvas_bruscas)                 AS curv,
            MAX(kv.velocidade_max)                 AS vmax,
            SUM(kv.excesso_velocidade_eventos)     AS speed_events,
            ROUND(SUM(kv.excesso_velocidade_segundos)/60.0,1) AS speed_min
        FROM km_viaturas kv
        JOIN viaturas v ON v.matricula = kv.matricula
        JOIN atribuicoes a ON a.viatura_id = v.id
            AND a.data = kv.data
        JOIN motoristas m ON m.id = a.motorista_id
        WHERE kv.data BETWEEN ? AND ?
        GROUP BY a.motorista_id
        HAVING km > 10
    """, (from_date, to_date)).fetchall()
    conn.close()

    def calc_score(trav_100, acel_100, curv_100, vmax):
        def pen(val, thresh):
            if val <= 0: return 25
            return round(25 * (1 - min(val / thresh, 1.0)))
        def pen_vmax(v):
            if v <= 120: return 25
            if v <= 130: return 20
            if v <= 140: return 13
            if v <= 160: return 6
            return 0
        return pen(trav_100, 8) + pen(acel_100, 8) + pen(curv_100, 8) + pen_vmax(vmax)

    scores = []
    for d in all_drivers:
        km = d['km'] or 1
        t100 = (d['trav'] or 0) / km * 100
        a100 = (d['acel'] or 0) / km * 100
        c100 = (d['curv'] or 0) / km * 100
        vmax = d['vmax'] or 0
        sc = calc_score(t100, a100, c100, vmax)
        scores.append({"motorista_id": d['motorista_id'], "nome": d['nome'], "score": sc})

    scores.sort(key=lambda x: x['score'], reverse=True)
    return {"scores": scores}


def get_comportamento_motorista(motorista_id, from_date, to_date):
    """
    Calcula score de comportamento do motorista baseado em dados Cartrack.
    Score 0-100 comparado com toda a frota no mesmo periodo.
    Metricas: travagens/100km, aceleracoes/100km, curvas/100km, velocidade_max
    """
    conn = get_db()
    c = conn.cursor()

    # Matriculas do motorista no periodo (aceita atribuicoes com hora vazia)
    matriculas = [r[0] for r in c.execute("""
        SELECT DISTINCT v.matricula FROM atribuicoes a
        JOIN viaturas v ON v.id = a.viatura_id
        WHERE a.motorista_id = ?
          AND a.data BETWEEN ? AND ?
          AND (
              (a.start_hora IS NULL OR a.start_hora = '')
              OR (substr(a.start_hora,1,10) <= ? AND substr(a.end_hora,1,10) >= ?)
          )
    """, (motorista_id, from_date, to_date, to_date, from_date)).fetchall()]

    if not matriculas:
        conn.close()
        return {"sem_dados": True}

    # Km oficial de kpis_diarios (mesma fonte que o resto do dashboard)
    km_row = c.execute("""
        SELECT ROUND(SUM(km_total),1) AS km_total
        FROM kpis_diarios
        WHERE motorista_id = ? AND data BETWEEN ? AND ?
    """, (motorista_id, from_date, to_date)).fetchone()

    km_oficial = float((km_row['km_total'] if km_row else None) or 0)
    if km_oficial <= 0:
        conn.close()
        return {"sem_dados": True}

    # Eventos comportamentais de km_viaturas com atribuicoes correctas
    row_m = c.execute("""
        SELECT
            SUM(kv.travagens_bruscas)                         AS travagens,
            SUM(kv.aceleracoes_bruscas)                       AS aceleracoes,
            SUM(kv.curvas_bruscas)                            AS curvas,
            ROUND(MAX(kv.velocidade_max),0)                   AS vmax,
            SUM(kv.excesso_velocidade_eventos)                AS speed_events,
            ROUND(SUM(kv.excesso_velocidade_segundos)/60.0,1) AS speed_min
        FROM km_viaturas kv
        JOIN viaturas v ON v.matricula = kv.matricula
        JOIN atribuicoes a ON a.viatura_id = v.id
            AND a.data = kv.data
            AND a.motorista_id = ?
            AND (
                (a.start_hora IS NULL OR a.start_hora = '')
                OR (substr(a.start_hora,1,10) <= kv.data AND substr(a.end_hora,1,10) >= kv.data)
            )
        WHERE kv.data BETWEEN ? AND ?
    """, (motorista_id, from_date, to_date)).fetchone()

    if not row_m:
        conn.close()
        return {"sem_dados": True}

    km_m = km_oficial
    if km_m <= 0:
        trav_m = acel_m = curv_m = speed_m = 0.0
    else:
        trav_m  = (row_m['travagens']    or 0) / km_m * 100
        acel_m  = (row_m['aceleracoes']  or 0) / km_m * 100
        curv_m  = (row_m['curvas']       or 0) / km_m * 100
        speed_m = (row_m['speed_events'] or 0) / km_m * 100
    vmax_m      = row_m['vmax'] or 0
    speed_min_m = float(row_m['speed_min'] or 0)

    # Dados de toda a frota (benchmark)
    rows_f = c.execute("""
        SELECT matricula,
               SUM(km_total)           AS km,
               SUM(travagens_bruscas)  AS trav,
               SUM(aceleracoes_bruscas) AS acel,
               SUM(curvas_bruscas)     AS curv,
               MAX(velocidade_max)     AS vmax
        FROM km_viaturas
        WHERE data BETWEEN ? AND ?
        GROUP BY matricula
        HAVING km > 10
    """, (from_date, to_date)).fetchall()

    # Calcular por motorista (via atribuicoes) para ranking
    # Simplificado: calcular metricas por viatura e ligar ao motorista
    all_drivers = c.execute("""
        SELECT
            a.motorista_id,
            m.nome,
            v.matricula,
            SUM(kv.km_total)                       AS km,
            SUM(kv.travagens_bruscas)              AS trav,
            SUM(kv.aceleracoes_bruscas)            AS acel,
            SUM(kv.curvas_bruscas)                 AS curv,
            MAX(kv.velocidade_max)                 AS vmax,
            SUM(kv.excesso_velocidade_eventos)     AS speed_events,
            ROUND(SUM(kv.excesso_velocidade_segundos)/60.0,1) AS speed_min
        FROM km_viaturas kv
        JOIN viaturas v ON v.matricula = kv.matricula
        JOIN atribuicoes a ON a.viatura_id = v.id
            AND a.data = kv.data
        JOIN motoristas m ON m.id = a.motorista_id
        WHERE kv.data BETWEEN ? AND ?
        GROUP BY a.motorista_id
        HAVING km > 10
    """, (from_date, to_date)).fetchall()

    conn.close()

    if not all_drivers:
        return {"sem_dados": True}

    # Calcular score para cada motorista (0-100, maior = melhor)
    def calc_score(trav_100, acel_100, curv_100, vmax, speed_100=0, speed_min=0):
        # Pesos: trav 30%, acel 30%, curv 15%, speed_events 10%, speed_time 10%, vmax 5%
        def pen(val, thresh, peso):
            if val <= 0: return peso
            return round(peso * (1 - min(val / thresh, 1.0)))
        def pen_vmax(v, peso):
            if v <= 120: return peso
            if v <= 130: return round(peso * 0.80)
            if v <= 140: return round(peso * 0.52)
            if v <= 160: return round(peso * 0.24)
            return 0
        def pen_speed_time(mins, peso):
            if mins <= 0: return peso
            return round(peso * (1 - min(mins / 60.0, 1.0)))
        return (pen(trav_100, 8, 30) + pen(acel_100, 8, 30) + pen(curv_100, 8, 15) +
                pen(speed_100, 10, 10) + pen_speed_time(speed_min, 10) + pen_vmax(vmax, 5))

    scores = []
    for d in all_drivers:
        km = d['km'] or 1
        t100 = (d['trav'] or 0) / km * 100
        a100 = (d['acel'] or 0) / km * 100
        c100 = (d['curv'] or 0) / km * 100
        vmax = d['vmax'] or 0
        sc = calc_score(t100, a100, c100, vmax)
        scores.append({
            "motorista_id": d['motorista_id'],
            "nome":         d['nome'],
            "score":        sc,
            "km":           round(km, 1),
            "trav_100":     round(t100, 2),
            "acel_100":     round(a100, 2),
            "curv_100":     round(c100, 2),
            "vmax":         vmax,
        })

    scores.sort(key=lambda x: x['score'], reverse=True)

    # Score e ranking do motorista
    score_m = calc_score(trav_m, acel_m, curv_m, vmax_m, speed_m, speed_min_m)
    ranking = next((i+1 for i, s in enumerate(scores) if s['motorista_id'] == motorista_id), None)
    total   = len(scores)
    percentil = round((1 - (ranking - 1) / total) * 100) if ranking else None

    def nota(sc, ranking, total):
        # Classificacao por percentil: top 30% bom, 31-60% regular, 61-80% atencao, 81-100% critico
        if total <= 0: pct = 50
        else: pct = round((ranking / total) * 100)
        if pct <= 30:  return {"label": "Bom Condutor", "cor": "#16a34a", "emoji": "🟢"}
        if pct <= 60:  return {"label": "Regular",      "cor": "#d97706", "emoji": "🟡"}
        if pct <= 80:  return {"label": "Atencao",      "cor": "#ea580c", "emoji": "🟠"}
        return               {"label": "Alerta Critico","cor": "#dc2626", "emoji": "🔴"}

    n = nota(score_m, ranking, total)

    return {
        "score":        score_m,
        "ranking":      ranking,
        "total":        total,
        "percentil":    percentil,
        "nota":         n["label"],
        "cor":          n["cor"],
        "emoji":        n["emoji"],
        "km_total":     round(km_oficial, 1),
        "trav_100km":   round(trav_m, 2),
        "acel_100km":   round(acel_m, 2),
        "curv_100km":   round(curv_m, 2),
        "vmax":         vmax_m,
        "speed_100":    round(speed_m, 2),
        "speed_min":    round(speed_min_m, 1),
        "metricas": {
            "trav":         {"val": row_m['travagens']     or 0, "por_100km": round(trav_m, 2)},
            "acel":         {"val": row_m['aceleracoes']   or 0, "por_100km": round(acel_m, 2)},
            "curv":         {"val": row_m['curvas']        or 0, "por_100km": round(curv_m, 2)},
            "vmax":         {"val": vmax_m},
            "speed_events": {"val": row_m['speed_events'] or 0, "por_100km": round(speed_m, 2)},
            "speed_min":    {"val": round(speed_min_m, 1)},
        },
        "media_frota": {
            "trav_100km": round(sum((d['trav'] or 0)/(d['km'] or 1)*100 for d in all_drivers)/len(all_drivers), 2),
            "acel_100km": round(sum((d['acel'] or 0)/(d['km'] or 1)*100 for d in all_drivers)/len(all_drivers), 2),
            "curv_100km": round(sum((d['curv'] or 0)/(d['km'] or 1)*100 for d in all_drivers)/len(all_drivers), 2),
            "vmax":       round(sum(d['vmax'] or 0 for d in all_drivers)/len(all_drivers), 1),
            "speed_100":  round(sum((d['speed_events'] or 0)/(d['km'] or 1)*100 for d in all_drivers)/len(all_drivers), 2),
            "speed_min":  round(sum(float(d['speed_min'] or 0) for d in all_drivers)/len(all_drivers), 1),
        }
    }


def get_kpis_hoje(date_str=None):
    """Constroi KPIs do dia actual a partir de Uber Live + Bolt do dia."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    c = conn.cursor()

    # Ultima captura Uber Live
    ultima = None
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faturacao_uber_live'").fetchone()
    if tbl:
        ultima = c.execute(
            "SELECT MAX(capturado_em) FROM faturacao_uber_live WHERE data=?", (date_str,)
        ).fetchone()[0]

    # Dados Uber Live
    uber_map = {}
    if ultima:
        rows_uber = c.execute("""
            SELECT ul.motorista_id, ul.nome_uber, m.nome, ul.faturacao_bruta,
                   ul.num_corridas, ul.tempo_online_min, ul.taxa_aceitacao, ul.taxa_cancelamento
            FROM faturacao_uber_live ul
            LEFT JOIN motoristas m ON m.id = ul.motorista_id
            WHERE ul.data=? AND ul.motorista_id IS NOT NULL
        """, (date_str,)).fetchall()
        for r in rows_uber:
            mid = r[0]
            if mid:
                uber_map[mid] = {
                    'nome': r[2] or r[1],
                    'fat_uber': float(r[3] or 0),
                    'corridas_uber': int(r[4] or 0),
                    'tempo_online': int(r[5] or 0),
                }

    # Dados Bolt do dia
    bolt_map = {}
    rows_bolt = c.execute("""
        SELECT fb.motorista_id, m.nome, fb.faturacao_liquida, fb.num_corridas
        FROM faturacao_bolt fb
        LEFT JOIN motoristas m ON m.id = fb.motorista_id
        WHERE fb.data=? AND fb.motorista_id IS NOT NULL
    """, (date_str,)).fetchall()
    for r in rows_bolt:
        mid = r[0]
        if mid:
            bolt_map[mid] = {
                'nome': r[1],
                'fat_bolt': float(r[2] or 0),
                'corridas_bolt': int(r[3] or 0),
            }

    # Atribuicoes do dia para viatura
    atr_map = {}
    rows_atr = c.execute("""
        SELECT a.motorista_id, v.matricula
        FROM atribuicoes a JOIN viaturas v ON v.id=a.viatura_id
        WHERE a.data=?
        ORDER BY a.start_hora DESC
    """, (date_str,)).fetchall()
    for r in rows_atr:
        if r[0] not in atr_map:
            atr_map[r[0]] = r[1]

    # Km do dia (Cartrack)
    km_map = {}
    rows_km = c.execute("""
        SELECT a.motorista_id, ROUND(SUM(kv.km_total),1)
        FROM km_viaturas kv
        JOIN viaturas v ON v.matricula=kv.matricula
        JOIN atribuicoes a ON a.viatura_id=v.id AND a.data=kv.data
        WHERE kv.data=?
        GROUP BY a.motorista_id
    """, (date_str,)).fetchall()
    for r in rows_km:
        km_map[r[0]] = float(r[1] or 0)

    conn.close()

    # Combinar todos os motoristas
    todos = set(list(uber_map.keys()) + list(bolt_map.keys()))
    kpis = []
    for mid in todos:
        u = uber_map.get(mid, {})
        b = bolt_map.get(mid, {})
        nome = u.get('nome') or b.get('nome') or str(mid)
        fat_uber = u.get('fat_uber', 0)
        fat_bolt = b.get('fat_bolt', 0)
        fat_total = fat_uber + fat_bolt
        corridas = u.get('corridas_uber', 0) + b.get('corridas_bolt', 0)
        km = km_map.get(mid, 0)
        ekm = round(fat_total / km, 3) if km > 1 else 0
        kpis.append({
            'motorista_id': mid,
            'nome_motorista': nome,
            'matricula': atr_map.get(mid, '-'),
            'faturacao_liquida': round(fat_total, 2),
            'fat_uber': round(fat_uber, 2),
            'fat_bolt': round(fat_bolt, 2),
            'num_corridas': corridas,
            'km_total': km,
            'receita_por_km': ekm,
            'gorjetas': 0, 'bonus': 0, 'via_verde_euros': 0,
            'corridas_bolt': b.get('corridas_bolt', 0),
            'corridas_uber': u.get('corridas_uber', 0),
            'tempo_online_min': u.get('tempo_online', 0),
            'taxa_aceitacao': 0, 'taxa_cancelamento': 0,
        })

    kpis.sort(key=lambda x: x['receita_por_km'], reverse=True)
    # Timestamps de ultima actualizacao
    bolt_update = c.execute("SELECT MAX(importado_em) FROM faturacao_bolt WHERE data=?", (date_str,)).fetchone()[0] or ''
    prio_update = c.execute("SELECT MAX(hora_transacao) FROM prio_transacoes WHERE data=?", (date_str,)).fetchone()[0] or ''
    if not prio_update:
        prio_update = c.execute("SELECT MAX(data_transacao) FROM prio_transacoes WHERE data=?", (date_str,)).fetchone()[0] or ''
    return {
        'data': date_str,
        'kpis_dia': kpis,
        'ultima_captura_uber': ultima,
        'ultima_captura_bolt': bolt_update,
        'ultima_captura_prio': prio_update,
        'fonte': 'live',
        'gerado_em': datetime.now().isoformat(),
        'datas_disponiveis': [],
        'historico_30d': [],
    }

def get_battery_soc():
    """Busca SoC de todos os VEs via Cartrack API — cache 10 min."""
    import os, base64, time as _time
    now = _time.time()
    if hasattr(get_battery_soc, '_cache') and now - get_battery_soc._ts < 600:
        return get_battery_soc._cache
    CARTRACK_USERNAME = "ADEL00005"
    CARTRACK_PASSWORD = os.environ.get("CARTRACK_PASSWORD", "")
    if not CARTRACK_PASSWORD:
        return {"vehicles": {}, "updated": "-", "error": "sem credenciais"}
    cred = base64.b64encode(f"{CARTRACK_USERNAME}:{CARTRACK_PASSWORD}".encode()).decode()
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            "https://fleetapi-pt.cartrack.com/rest/vehicles/soc/latest?limit=200",
            headers={"Authorization": f"Basic {cred}", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read())
        lista = data if isinstance(data, list) else data.get("vehicles", data.get("data", []))
        result = {}
        for v in lista:
            reg = v.get("registration") or v.get("vehicleRegistration") or ""
            soc = v.get("battery_percentage_left") or v.get("soc") or v.get("stateOfCharge")
            if reg and soc is not None:
                soc_val = int(soc)
                if 0 <= soc_val <= 100:
                    result[reg] = soc_val
        get_battery_soc._cache = {"vehicles": result, "updated": datetime.now().strftime("%H:%M"), "total": len(result)}
        get_battery_soc._ts = now
        return get_battery_soc._cache
    except Exception as e:
        return {"vehicles": {}, "updated": "-", "error": str(e)}

def get_vext_status():
    """Devolve tensao bateria auxiliar (12V) por viatura via Cartrack.
    Alerta se vext < 11.5V (aviso) ou < 10V (critico).
    """
    import time as _time
    now = _time.time()
    cache = getattr(get_vext_status, '_cache', None)
    ts    = getattr(get_vext_status, '_ts', 0)
    if cache and now - ts < 300:
        return cache

    CARTRACK_USERNAME = "ADEL00005"
    CARTRACK_PASSWORD = os.environ.get("CARTRACK_PASSWORD", "")
    if not CARTRACK_PASSWORD:
        return {"vehicles": {}, "updated": "-"}
    cred = base64.b64encode(f"{CARTRACK_USERNAME}:{CARTRACK_PASSWORD}".encode()).decode()
    try:
        import urllib.request, json as _json
        page = 1
        result = {}
        while True:
            req = urllib.request.Request(
                f"https://fleetapi-pt.cartrack.com/rest/vehicles/status?page={page}&limit=100",
                headers={"Authorization": f"Basic {cred}", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = _json.loads(r.read())
            lista = data.get("data", [])
            if not lista:
                break
            for v in lista:
                reg  = v.get("registration", "")
                vext = v.get("vext")
                if reg and vext is not None:
                    try:
                        vext_val = round(float(vext), 2)
                        status = "ok"
                        if vext_val < 10.0:
                            status = "critico"
                        elif vext_val < 11.5:
                            status = "aviso"
                        result[reg] = {"vext": vext_val, "status": status}
                    except:
                        pass
            if len(lista) < 100:
                break
            page += 1
        get_vext_status._cache = {"vehicles": result, "updated": datetime.now().strftime("%H:%M"), "total": len(result)}
        get_vext_status._ts = now
        return get_vext_status._cache
    except Exception as e:
        return {"vehicles": {}, "updated": "-", "error": str(e)}

def get_vext_status():
    """Devolve tensao bateria auxiliar (12V) por viatura via Cartrack.
    Alerta se vext < 11.5V (aviso) ou < 10V (critico).
    """
    import time as _time
    now = _time.time()
    cache = getattr(get_vext_status, '_cache', None)
    ts    = getattr(get_vext_status, '_ts', 0)
    if cache and now - ts < 300:
        return cache

    CARTRACK_USERNAME = "ADEL00005"
    CARTRACK_PASSWORD = os.environ.get("CARTRACK_PASSWORD", "")
    if not CARTRACK_PASSWORD:
        return {"vehicles": {}, "updated": "-"}
    cred = base64.b64encode(f"{CARTRACK_USERNAME}:{CARTRACK_PASSWORD}".encode()).decode()
    try:
        import urllib.request, json as _json
        page = 1
        result = {}
        while True:
            req = urllib.request.Request(
                f"https://fleetapi-pt.cartrack.com/rest/vehicles/status?page={page}&limit=100",
                headers={"Authorization": f"Basic {cred}", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = _json.loads(r.read())
            lista = data.get("data", [])
            if not lista:
                break
            for v in lista:
                reg  = v.get("registration", "")
                vext = v.get("vext")
                if reg and vext is not None:
                    try:
                        vext_val = round(float(vext), 2)
                        status = "ok"
                        if vext_val < 10.0:
                            status = "critico"
                        elif vext_val < 11.5:
                            status = "aviso"
                        result[reg] = {"vext": vext_val, "status": status}
                    except:
                        pass
            if len(lista) < 100:
                break
            page += 1
        get_vext_status._cache = {"vehicles": result, "updated": datetime.now().strftime("%H:%M"), "total": len(result)}
        get_vext_status._ts = now
        return get_vext_status._cache
    except Exception as e:
        return {"vehicles": {}, "updated": "-", "error": str(e)}

def get_uber_live(date_str=None):
    """Devolve dados da ultima captura Uber Live para o dia actual."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    c = conn.cursor()
    # Verificar se tabela existe
    tbl = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faturacao_uber_live'").fetchone()
    if not tbl:
        conn.close()
        return {"data": date_str, "ultima_captura": None, "motoristas": []}
    # Ultima captura do dia
    ultima = c.execute(
        "SELECT MAX(capturado_em) FROM faturacao_uber_live WHERE data=?", (date_str,)
    ).fetchone()[0]
    if not ultima:
        conn.close()
        return {"data": date_str, "ultima_captura": None, "motoristas": []}
    # Dados da ultima captura
    rows = c.execute("""
        SELECT
            ul.nome_uber,
            m.nome,
            ul.motorista_id,
            ul.faturacao_bruta,
            ul.num_corridas,
            ul.tempo_online_min,
            ul.taxa_aceitacao,
            ul.taxa_cancelamento
        FROM faturacao_uber_live ul
        LEFT JOIN motoristas m ON m.id = ul.motorista_id
        WHERE ul.data=?
        ORDER BY ul.faturacao_bruta DESC
    """, (date_str, ultima)).fetchall()
    conn.close()
    total_fat = sum(float(r[3] or 0) for r in rows)
    total_corr = sum(int(r[4] or 0) for r in rows)
    return {
        "data": date_str,
        "ultima_captura": ultima,
        "total_faturacao": round(total_fat, 2),
        "total_corridas": total_corr,
        "n_motoristas": len(rows),
        "motoristas": [
            {
                "nome": r[1] or r[0],
                "nome_uber": r[0],
                "motorista_id": r[2],
                "faturacao": round(float(r[3] or 0), 2),
                "corridas": int(r[4] or 0),
                "horas_online": round(float(r[5] or 0) / 60, 1),
                "taxa_aceitacao": round(float(r[6] or 0), 1),
                "taxa_cancelamento": round(float(r[7] or 0), 1),
            }
            for r in rows
        ]
    }

# ── HTTP HANDLER ────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{args[1]}] {args[0]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, filename, status=200):
        path = os.path.join(BASE_DIR, filename)
        try:
            body = open(path, "rb").read()
            self.send_response(status)
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def send_html_str(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def is_authenticated(self):
        return get_session(self.headers.get("Cookie")) is not None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)
        clean_sessions()

        # ── MAPA DA FROTA ─────────────────────────────────
        if path == '/mapa':
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(open('/opt/tvde/static/mapa.html','rb').read())
            return
        if path == '/api/frota/mapa':
            import sqlite3 as _sq, json as _js
            from datetime import datetime as _dt
            _con = _sq.connect('/opt/tvde/tvde_data.db')
            _con2 = _sq.connect('/opt/tvde/tvde_data.db')
            _con.row_factory = _sq.Row
            try: _rows = _con.execute('SELECT * FROM frota_mapa').fetchall()
            except: _rows = []
            _con.close()
            _now = _dt.now()
            _carros = []
            for _r in _rows:
                _pm = 0
                if _r['parado_desde']:
                    try: _pm = int((_now-_dt.fromisoformat(_r['parado_desde'])).total_seconds()//60)
                    except: pass
                # faturacao e km semanais por matricula
                _fat = 0
                _km = 0
                # motorista mais recente desta viatura hoje
                _mid_row = _con2.execute(
                    "SELECT a.motorista_id FROM atribuicoes a "
                    "JOIN viaturas vt ON vt.id=a.viatura_id "
                    "WHERE vt.matricula=? AND a.data=date('now') "
                    "ORDER BY a.id DESC LIMIT 1",
                    (_r['matricula'],)).fetchone()
                _mid = _mid_row[0] if _mid_row else None
                _row_f = None
                if _mid:
                    _bolt = _con2.execute(
                        "SELECT COALESCE(SUM(faturacao_liquida),0) FROM faturacao_bolt "
                        "WHERE motorista_id=? AND data>=date('now','weekday 1','-7 days')",
                        (_mid,)).fetchone()[0] or 0
                    _uber = _con2.execute(
                        "SELECT COALESCE(SUM(faturacao_bruta),0) FROM faturacao_uber_live "
                        "WHERE motorista_id=? AND data>=date('now','weekday 1','-7 days')",
                        (_mid,)).fetchone()[0] or 0
                    _km = _con2.execute(
                        "SELECT COALESCE(SUM(km_total),0) FROM km_viaturas "
                        "WHERE matricula=? AND data>=date('now','weekday 1','-7 days')",
                        (_r['matricula'],)).fetchone()[0] or 0
                    _row_f = (_bolt + _uber, _km)
                if _row_f:
                    _fat = round(_row_f[0] or 0, 2)
                    _km  = round(_row_f[1] or 0, 1)
                _carros.append({'id':_r['matricula'],'motorista':_r['motorista'] or '-',
                    'lat':_r['lat'],'lng':_r['lng'],'vel':_r['velocidade'],
                    'app':_r['app'],'movendo':bool(_r['movendo']),'paradoMin':_pm,
                    'alerta':(not _r['movendo'] and _pm>=30),
                    'atualizado':_r['atualizado_em'],'fat_semana':_fat,'km_semana':_km})
            _body = _js.dumps({'carros':_carros,'ts':_now.isoformat(timespec='seconds')}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(_body)
            return
        # ── FIM MAPA ───────────────────────────────────────────
        # Login page
        if path == "/login":
            self.send_html_str(LOGIN_HTML.replace("{error}", ""))
            return

        # Logout
        if path == "/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("tvde_session="):
                    token = part[len("tvde_session="):]
                    SESSIONS.pop(token, None)
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "tvde_session=; Max-Age=0; Path=/")
            self.end_headers()
            return

        # Protect all other routes
        if path == "/api/vext":
            try: self.send_json(get_vext_status())
            except Exception as e: self.send_json({"error": str(e)}, 500)
            return
        if path == "/api/vext":
            try: self.send_json(get_vext_status())
            except Exception as e: self.send_json({"error": str(e)}, 500)
            return
        if path == "/api/battery":
            try: self.send_json(get_battery_soc())
            except Exception as e: self.send_json({"error": str(e)}, 500)
            return

        if path == "/logomovvi.jpg":
            try:
                body = open(os.path.join(BASE_DIR, "logomovvi.jpg"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        # ── PWA: manifest.json ────────────────────────────────────
        if path == "/manifest.json":
            try:
                body = open(os.path.join(BASE_DIR, "manifest.json"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/manifest+json")
                self.send_header("Content-Length", len(body))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        # ── PWA: service worker ───────────────────────────────────
        if path == "/static/sw.js":
            try:
                body = open(os.path.join(BASE_DIR, "static", "sw.js"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", len(body))
                self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
                self.send_header("Service-Worker-Allowed", "/")
                self.end_headers()
                self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        # ── PWA: ícones ───────────────────────────────────────────
        if path in ("/static/icon-192.png", "/static/icon-512.png"):
            filename = path.split("/")[-1]
            try:
                body = open(os.path.join(BASE_DIR, "static", filename), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", len(body))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                self.wfile.write(body)
            except:
                self.send_response(404); self.end_headers()
            return

        # AREA DO MOTORISTA
        if path == "/motorista":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.redirect("/motorista/login")
                return
            self.send_html("motorista_dashboard.html")
            return

        if path == "/motorista/login":
            self.send_html_str(MOTOR_LOGIN_HTML.replace("{error}", ""))
            return

        if path == "/motorista/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("tvde_motor="):
                    token = part[len("tvde_motor="):]
                    MOTOR_SESSIONS.pop(token, None)
            self.send_response(302)
            self.send_header("Location", "/motorista/login")
            self.send_header("Set-Cookie", "tvde_motor=; Max-Age=0; Path=/")
            self.end_headers()
            return

        if path == "/api/motorista/kpis":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401)
                return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            try:
                self.send_json(get_kpis_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/motorista/ranking":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401); return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"posicao": None, "total": 0}); return
            try:
                self.send_json(get_ranking_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return


        if path == "/api/motorista/comportamento":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401); return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"sem_dados": True}); return
            try:
                self.send_json(get_comportamento_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return
        if path == "/api/motorista/prio/detalhe":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401); return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"transacoes": []}); return
            try:
                self.send_json(get_prio_motorista_detalhe(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/motorista/prio":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401)
                return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"kwh": 0, "valor": 0, "n_carregamentos": 0})
                return
            try:
                self.send_json(get_prio_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if not self.is_authenticated():
            self.redirect("/login")
            return

        if path in ("/", "/dashboard"):
            self.send_html("tvde_dashboard_v2.html")
        elif path == "/api/kpis":
            date_str  = qs.get("data",  [None])[0]
            from_date = qs.get("from",  [None])[0]
            to_date   = qs.get("to",    [None])[0]
            try: self.send_json(get_kpis(date_str, from_date, to_date))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/datas":
            self.send_json(get_datas_disponiveis())
        elif path == "/api/prio":
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            try: self.send_json(get_prio(from_date, to_date))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/comportamento":
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            try: self.send_json(get_comportamento_frota(from_date, to_date))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/viaverde":
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            try: self.send_json(get_viaverde(from_date, to_date))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/check":
            date_str = qs.get("data", [datetime.now().strftime("%Y-%m-%d")])[0]
            try: self.send_json(get_duplo_check(date_str))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/check/uber":
            try: self.send_json(get_uber_check_historico())
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/uber_live":
            date_str = qs.get("data", [datetime.now().strftime("%Y-%m-%d")])[0]
            try: self.send_json(get_uber_live(date_str))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/vext":
            try: self.send_json(get_vext_status())
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/vext":
            try: self.send_json(get_vext_status())
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/battery":
            try: self.send_json(get_battery_soc())
            except Exception as e: self.send_json({"error": str(e)}, 500)
        elif path == "/api/kpis_hoje":
            date_str = qs.get("data", [datetime.now().strftime("%Y-%m-%d")])[0]
            try: self.send_json(get_kpis_hoje(date_str))
            except Exception as e: self.send_json({"error": str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))

        # Login handler
        if path == "/login":
            body = self.rfile.read(length).decode("utf-8")
            params = {}
            for part in body.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = requests.utils.unquote(v).replace("+", " ")
            username = params.get("username", "").strip().lower()
            password = params.get("password", "")
            user = USERS.get(username)
            if user and user["hash"] == hashlib.sha256(password.encode()).hexdigest():
                token = create_session(username)
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie",
                    f"tvde_session={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
            else:
                html = LOGIN_HTML.replace("{error}", '<p class="err">Utilizador ou password incorrectos</p>')
                self.send_html_str(html, 401)
            return

        if path == "/motorista/login":
            body = self.rfile.read(length).decode("utf-8")
            params = {}
            for part in body.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = requests.utils.unquote(v).replace("+", " ")
            email = params.get("email", "").strip().lower()
            password = params.get("password", "")
            conn = get_db()
            row = conn.execute(
                "SELECT motorista_id, password_hash, primeiro_acesso FROM emails_motoristas WHERE LOWER(email)=? AND activo=1",
                (email,)
            ).fetchone()
            conn.close()
            if not row:
                self.send_html_str(MOTOR_LOGIN_HTML.replace("{error}", '<p class="err">Email nao encontrado</p>'), 401)
                return
            primeiro = row[2] if row[2] is not None else 1
            phash = row[1]
            if primeiro or not phash:
                self.send_html_str(MOTOR_SET_PASS_HTML.replace("{email}", email).replace("{error}", ""))
                return
            if phash == hashlib.sha256(password.encode()).hexdigest():
                token = create_motor_session(row[0], email)
                self.send_response(302)
                self.send_header("Location", "/motorista")
                self.send_header("Set-Cookie", f"tvde_motor={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
            else:
                self.send_html_str(MOTOR_LOGIN_HTML.replace("{error}", '<p class="err">Password incorrecta</p>'), 401)
            return

        if path == "/motorista/change-password":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"ok": False, "erro": "Sessao expirada"}, 401)
                return
            body = self.rfile.read(length).decode("utf-8")
            params = {}
            for part in body.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = requests.utils.unquote(v).replace("+", " ")
            pw1 = params.get("password", "")
            pw2 = params.get("password2", "")
            if pw1 != pw2 or len(pw1) < 6:
                self.send_json({"ok": False, "erro": "Passwords nao coincidem ou minimo 6 caracteres"})
                return
            h = hashlib.sha256(pw1.encode()).hexdigest()
            conn = get_db()
            conn.execute(
                "UPDATE emails_motoristas SET password_hash=?, primeiro_acesso=0 WHERE motorista_id=?",
                (h, sess["motorista_id"])
            )
            conn.commit()
            conn.close()
            self.send_json({"ok": True})
            return

        if path == "/motorista/set-password":
            body = self.rfile.read(length).decode("utf-8")
            params = {}
            for part in body.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = requests.utils.unquote(v).replace("+", " ")
            email = params.get("email", "").strip().lower()
            pw1 = params.get("password", "")
            pw2 = params.get("password2", "")
            if pw1 != pw2 or len(pw1) < 6:
                self.send_html_str(MOTOR_SET_PASS_HTML.replace("{email}", email).replace("{error}", '<p class="err">Passwords nao coincidem ou min. 6 caracteres</p>'), 400)
                return
            h = hashlib.sha256(pw1.encode()).hexdigest()
            conn = get_db()
            row = conn.execute(
                "SELECT motorista_id FROM emails_motoristas WHERE LOWER(email)=? AND activo=1",
                (email,)
            ).fetchone()
            if not row:
                conn.close()
                self.redirect("/motorista/login")
                return
            conn.execute("UPDATE emails_motoristas SET password_hash=?, primeiro_acesso=0 WHERE LOWER(email)=?", (h, email))
            conn.commit()
            token = create_motor_session(row[0], email)
            conn.close()
            self.send_response(302)
            self.send_header("Location", "/motorista")
            self.send_header("Set-Cookie", f"tvde_motor={token}; Max-Age={SESSION_TTL}; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        # Protect POST routes
        # AREA DO MOTORISTA
        if path == "/motorista":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.redirect("/motorista/login")
                return
            self.send_html("motorista_dashboard.html")
            return

        if path == "/motorista/login":
            self.send_html_str(MOTOR_LOGIN_HTML.replace("{error}", ""))
            return

        if path == "/motorista/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("tvde_motor="):
                    token = part[len("tvde_motor="):]
                    MOTOR_SESSIONS.pop(token, None)
            self.send_response(302)
            self.send_header("Location", "/motorista/login")
            self.send_header("Set-Cookie", "tvde_motor=; Max-Age=0; Path=/")
            self.end_headers()
            return

        if path == "/api/motorista/kpis":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401)
                return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            try:
                self.send_json(get_kpis_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/motorista/ranking":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401); return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"posicao": None, "total": 0}); return
            try:
                self.send_json(get_ranking_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/motorista/prio/detalhe":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401); return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"transacoes": []}); return
            try:
                self.send_json(get_prio_motorista_detalhe(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if path == "/api/motorista/prio":
            sess = get_motor_session(self.headers.get("Cookie"))
            if not sess:
                self.send_json({"error": "nao autenticado"}, 401)
                return
            from_date = qs.get("from", [None])[0]
            to_date   = qs.get("to",   [None])[0]
            if not from_date or not to_date:
                self.send_json({"kwh": 0, "valor": 0, "n_carregamentos": 0})
                return
            try:
                self.send_json(get_prio_motorista(sess["motorista_id"], from_date, to_date))
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if not self.is_authenticated():
            self.send_response(401); self.end_headers()
            return

        body = json.loads(self.rfile.read(length) or b"{}")
        if path == "/api/drivers/save":
            self.send_json({"ok": True})
        elif path == "/api/drivers/delete":
            self.send_json({"ok": True})
        else:
            self.send_response(404); self.end_headers()


# ── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TVDE Fleet Server")
    print(f"  http://localhost:{PORT}")
    print(f"  Login: http://localhost:{PORT}/login")
    print(f"  Logout: http://localhost:{PORT}/logout")
    print("  Ctrl+C para parar")
    print("=" * 55)
class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

ReusableHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

# ══════════════════════════════════════════════════════════════════
# MAPA DA FROTA — colar no fim do /opt/tvde/servidor.py
# (antes do app.run, se existir)
# Rotas: /mapa (pagina) e /api/frota/mapa (dados, 15s)
# ══════════════════════════════════════════════════════════════════

import sqlite3 as _sql_mapa
from datetime import datetime as _dt_mapa
from flask import jsonify as _json_mapa, send_file as _send_mapa

_FROTA_DB = "/opt/tvde/tvde_data.db"
_PARADO_ALERTA_MIN = 30


@app.route("/api/frota/mapa")
def api_frota_mapa():
    con = _sql_mapa.connect(_FROTA_DB)
    con.row_factory = _sql_mapa.Row
    try:
        rows = con.execute("SELECT * FROM frota_mapa").fetchall()
    except _sql_mapa.OperationalError:
        rows = []   # daemon ainda nao correu
    con.close()

    agora = _dt_mapa.now()
    carros = []
    for r in rows:
        parado_min = 0
        if r["parado_desde"]:
            try:
                parado_min = int((agora - _dt_mapa.fromisoformat(
                    r["parado_desde"])).total_seconds() // 60)
            except ValueError:
                pass
        carros.append({
            "id": r["matricula"],
            "motorista": r["motorista"] or "—",
            "lat": r["lat"], "lng": r["lng"],
            "vel": r["velocidade"],
            "app": r["app"],
            "movendo": bool(r["movendo"]),
            "paradoMin": parado_min,
            "alerta": (r["app"] == "offline" and not r["movendo"]
                       and parado_min >= _PARADO_ALERTA_MIN),
            "atualizado": r["atualizado_em"],
        })
    return _json_mapa({"carros": carros,
                       "ts": agora.isoformat(timespec="seconds")})


@app.route("/mapa")
def pagina_mapa():
    return _send_mapa("/opt/tvde/static/mapa.html")
