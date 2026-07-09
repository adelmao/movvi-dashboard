#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI TVDE FLEET — Módulo de Lavagens (integrado no servidor.py)
================================================================
Corre DENTRO do dashboard.movvi.com.pt (porta 5000), na mesma BD.
Motoristas e viaturas vêm diretamente do SQLite do sistema — novos
motoristas aparecem automaticamente.

ROTAS ADICIONADAS AO DASHBOARD:
  https://dashboard.movvi.com.pt/lavagens                  → página do motorista
  https://dashboard.movvi.com.pt/lavagens/admin?chave=...  → painel de gestão
  /api/lavagens/*                                          → API interna

INTEGRAÇÃO (3 linhas no servidor.py — ou use instalar_lavagens.py):
  1. No topo, junto aos imports:        import lavagens_mod
  2. Primeira linha de do_GET(self):    if lavagens_mod.handle(self, "GET"): return
  3. Primeira linha de do_POST(self):   if lavagens_mod.handle(self, "POST"): return
"""

import json, os, sqlite3, base64, re, unicodedata
from datetime import datetime, timedelta

# ─────────── CONFIGURAÇÃO ───────────
DB_PATH     = "/opt/tvde/tvde_data.db"
FOTOS_DIR   = "/opt/tvde/dashboard/fotos_lavagens"
CHAVE_ADMIN = "movvi_2026"          # mude isto!
CICLO       = 5                    # lavagens por incentivo

# None = deteção automática das tabelas do seu sistema
TABELA_MOTORISTAS = None   # ex: "motoristas"
COLUNA_NOME       = None   # ex: "nome"
TABELA_VIATURAS   = None   # ex: "viaturas"
COLUNA_MATRICULA  = None   # ex: "matricula"
COLUNA_MODELO     = None   # ex: "modelo"
# ─────────────────────────────────────

os.makedirs(FOTOS_DIR, exist_ok=True)
_inicializado = False


LAVAGENS_DB = "/opt/tvde/dashboard/lavagens.db"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def dbl():
    conn = sqlite3.connect(LAVAGENS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    global _inicializado
    if _inicializado:
        return
    with dbl() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lavagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motorista TEXT NOT NULL,
            matricula TEXT,
            tipo TEXT DEFAULT 'exterior',
            foto TEXT,
            data TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS lavagens_premios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motorista TEXT NOT NULL,
            data TEXT NOT NULL
        )""")
    _detectar_esquema()
    _inicializado = True


def _detectar_esquema():
    global TABELA_MOTORISTAS, COLUNA_NOME, TABELA_VIATURAS, COLUNA_MATRICULA, COLUNA_MODELO
    with db() as c:
        tabelas = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]

        def colunas(t):
            return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]

        if not TABELA_MOTORISTAS:
            for t in tabelas:
                if "motorista" in t.lower():
                    cols = colunas(t)
                    for col in ("nome", "name", "motorista", "nome_completo"):
                        if col in cols:
                            TABELA_MOTORISTAS, COLUNA_NOME = t, col
                            break
                if TABELA_MOTORISTAS:
                    break

        if not TABELA_VIATURAS:
            for t in tabelas:
                if ("viatura" in t.lower() or "vehicle" in t.lower()) and t != "lavagens":
                    cols = colunas(t)
                    for col in ("matricula", "registration", "licence_plate", "placa"):
                        if col in cols:
                            TABELA_VIATURAS, COLUNA_MATRICULA = t, col
                            for m in ("modelo", "model", "make", "nome"):
                                if m in cols:
                                    COLUNA_MODELO = m
                                    break
                            break
                if TABELA_VIATURAS:
                    break

    print(f"[lavagens] motoristas: {TABELA_MOTORISTAS}.{COLUNA_NOME} | "
          f"viaturas: {TABELA_VIATURAS}.{COLUNA_MATRICULA} ({COLUNA_MODELO})")


def get_motoristas():
    if not TABELA_MOTORISTAS:
        return []
    with db() as c:
        rows = c.execute(
            f"SELECT DISTINCT {COLUNA_NOME} AS n FROM {TABELA_MOTORISTAS} "
            f"WHERE {COLUNA_NOME} IS NOT NULL AND TRIM({COLUNA_NOME}) != '' AND ativo = 1 "
            f"ORDER BY {COLUNA_NOME}").fetchall()
    return [r["n"] for r in rows]


def get_viaturas():
    if not TABELA_VIATURAS:
        return []
    sel = f"{COLUNA_MATRICULA} AS m" + (f", {COLUNA_MODELO} AS mod" if COLUNA_MODELO else ", '' AS mod")
    with db() as c:
        rows = c.execute(
            f"SELECT DISTINCT {sel} FROM {TABELA_VIATURAS} "
            f"WHERE {COLUNA_MATRICULA} IS NOT NULL ORDER BY {COLUNA_MATRICULA}").fetchall()
    return [{"matricula": r["m"], "modelo": r["mod"] or ""} for r in rows]


def _semana_atual():
    hoje = datetime.now()
    return (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")


def _slug(s):
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def estado_motorista(nome):
    _init()
    with dbl() as c:
        total = c.execute("SELECT COUNT(*) FROM lavagens WHERE motorista=?", (nome,)).fetchone()[0]
        premios = c.execute("SELECT COUNT(*) FROM lavagens_premios WHERE motorista=?", (nome,)).fetchone()[0]
        semana = c.execute(
            "SELECT COUNT(*) FROM lavagens WHERE motorista=? AND date(data) >= ?",
            (nome, _semana_atual())).fetchone()[0]
        hist = c.execute(
            "SELECT data, tipo, matricula, foto FROM lavagens WHERE motorista=? "
            "ORDER BY data DESC LIMIT 12", (nome,)).fetchall()
    ciclo = total - premios * CICLO
    return {
        "total": total, "premios": premios,
        "ciclo": max(0, min(ciclo, CICLO)), "pendente": ciclo >= CICLO,
        "semana": semana > 0,
        "historico": [dict(h) for h in hist],
    }


# ─────────── PONTO DE ENTRADA (chamado pelo servidor.py) ───────────

def handle(h, method):
    """Devolve True se o pedido era do módulo de lavagens (e foi tratado)."""
    path = h.path.split("?")[0]
    host = (h.headers.get("Host") or "").split(":")[0]
    if host.startswith("lavagens.") and path == "/":
        path = "/lavagens"
    if not (path == "/lavagens" or path.startswith("/lavagens/")
            or path.startswith("/api/lavagens")):
        return False

    _init()
    qs = dict(p.split("=", 1) for p in h.path.split("?")[1].split("&")) \
        if "?" in h.path else {}

    def send(body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode() if isinstance(body, str) else body
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def jout(obj, code=200):
        send(json.dumps(obj, ensure_ascii=False),
             "application/json; charset=utf-8", code)

    if method == "GET":
        if path == "/lavagens":
            send(PAGINA_MOTORISTA)
        elif path == "/lavagens/admin":
            if qs.get("chave") != CHAVE_ADMIN:
                send("<h3>Acesso negado. Use /lavagens/admin?chave=SUA_CHAVE</h3>", code=403)
            else:
                send(PAGINA_ADMIN.replace("__CHAVE__", CHAVE_ADMIN))
        elif path == "/api/lavagens/dados":
            jout({"motoristas": get_motoristas(), "viaturas": get_viaturas(), "ciclo": CICLO})
        elif path == "/api/lavagens/estado":
            nome = base64.b64decode(qs.get("motorista", "")).decode() if qs.get("motorista") else ""
            jout(estado_motorista(nome))
        elif path == "/api/lavagens/frota":
            if qs.get("chave") != CHAVE_ADMIN:
                return jout({"erro": "chave inválida"}, 403) or True
            out = []
            for nome in get_motoristas():
                e = estado_motorista(nome)
                e["nome"] = nome
                e.pop("historico")
                out.append(e)
            out.sort(key=lambda x: (-x["pendente"], -x["total"]))
            jout(out)
        elif path == "/lavagens/qr":
            if qs.get("chave") != CHAVE_ADMIN:
                send("Acesso negado", code=403)
            elif os.path.exists("/opt/tvde/dashboard/qr_lavagens.html"):
                send(open("/opt/tvde/dashboard/qr_lavagens.html", encoding="utf-8").read())
            else:
                send("Folha ainda nao gerada. Corra: /opt/tvde/venv/bin/python /opt/tvde/gerar_qr.py", code=404)
        elif path.startswith("/lavagens/fotos/"):
            f = os.path.join(FOTOS_DIR, os.path.basename(path))
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    send(fh.read(), "image/jpeg")
            else:
                send("nao encontrada", code=404)
        else:
            send("404", code=404)
        return True

    if method == "POST":
        try:
            length = int(h.headers.get("Content-Length", 0))
            body = json.loads(h.rfile.read(length))
        except Exception:
            jout({"erro": "json inválido"}, 400)
            return True

        if path == "/api/lavagens/registar":
            nome = body.get("motorista", "").strip()
            if not nome:
                jout({"erro": "motorista em falta"}, 400)
                return True
            foto_nome = None
            if body.get("foto"):
                raw = base64.b64decode(body["foto"].split(",")[-1])
                foto_nome = f"{_slug(nome)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
                with open(os.path.join(FOTOS_DIR, foto_nome), "wb") as f:
                    f.write(raw)
            with dbl() as c:
                c.execute(
                    "INSERT INTO lavagens (motorista, matricula, tipo, foto, data) VALUES (?,?,?,?,?)",
                    (nome, body.get("matricula", ""), body.get("tipo", "exterior"),
                     foto_nome, datetime.now().isoformat()))
            jout(estado_motorista(nome))

        elif path == "/api/lavagens/resgatar":
            if body.get("chave") != CHAVE_ADMIN:
                jout({"erro": "chave inválida"}, 403)
                return True
            nome = body.get("motorista", "")
            if not estado_motorista(nome)["pendente"]:
                jout({"erro": "sem incentivo pendente"}, 400)
                return True
            with dbl() as c:
                c.execute("INSERT INTO lavagens_premios (motorista, data) VALUES (?,?)",
                          (nome, datetime.now().isoformat()))
            jout(estado_motorista(nome))
        else:
            jout({"erro": "rota desconhecida"}, 404)
        return True

    return False


# ─────────── PÁGINAS ───────────

_BASE_CSS = """
*{box-sizing:border-box;margin:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#EEF3F8;color:#0E1B2C;
     max-width:480px;margin:0 auto;padding:14px 14px 40px}
header{display:flex;align-items:center;gap:10px;padding:8px 0 16px}
header img{height:36px}
header .sub{font-size:11px;color:#6B7A8C;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.card{background:#fff;border-radius:18px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(14,27,44,.08)}
h1{font-size:19px;margin-bottom:6px}
select,input,button{width:100%;padding:13px;border-radius:12px;font-size:16px;font-family:inherit}
select,input{border:1.5px solid #C9D4E0;margin-bottom:10px;background:#fff}
button{border:none;background:#1B7FE4;color:#fff;font-weight:700;cursor:pointer}
button:active{transform:scale(.98)}
.muted{color:#6B7A8C;font-size:14px}
.selos{display:flex;justify-content:center;gap:12px;margin:18px 0}
.selo{width:46px;height:46px;border-radius:50%;border:2px solid #C9D4E0;display:flex;
      align-items:center;justify-content:center;font-weight:800;color:#93A3B5;background:#fff}
.selo.ok{background:#1B7FE4;border-color:#1B7FE4;color:#fff}
.premio{background:#EDFAF3;border:1.5px solid #B7E8CE;border-radius:14px;padding:16px;
        text-align:center;margin-top:10px;font-weight:700}
.ok-semana{background:#EDFAF3;color:#17825A;font-weight:600;font-size:13px;
           padding:8px;border-radius:10px;text-align:center;margin-bottom:10px}
.hist{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.hist img{width:100%;height:80px;object-fit:cover;border-radius:10px;display:block}
.hist .meta{font-size:10.5px;color:#6B7A8C;font-weight:600;padding-top:3px}
.tipos{display:flex;gap:8px;margin-bottom:12px}
.tipos button{background:#fff;color:#6B7A8C;border:1.5px solid #C9D4E0;font-size:13px;padding:9px 0;text-transform:capitalize}
.tipos button.on{border-color:#1B7FE4;background:#F0F6FF;color:#1B7FE4}
.linha{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #EEF3F8}
.linha .nome{font-weight:700;font-size:14px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.cnt{font-weight:800;font-size:13px;min-width:44px;text-align:right}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:#0E1B2C;
       color:#fff;padding:11px 20px;border-radius:12px;font-size:14px;font-weight:600;z-index:9}
"""

_LOGO = "https://movvi.com.pt/assets/website/assets/img/logo.png"

PAGINA_MOTORISTA = """<!DOCTYPE html><html lang="pt"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movvi · Lavagens</title><style>""" + _BASE_CSS + """</style></head><body>
<header><img src=\"""" + _LOGO + """\" alt="Movvi"><div class="sub">Cartão de Lavagens</div></header>

<div class="card" id="login">
  <h1>Identificação</h1>
  <p class="muted" style="margin-bottom:12px">Registe as suas lavagens semanais. A cada 5, ganha um incentivo.</p>
  <select id="selMot"><option value="">Motorista…</option></select>
  <select id="selVia"><option value="">Viatura…</option></select>
  <button onclick="entrar()">Entrar</button>
</div>

<div class="card" id="painel" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div><div id="pNome" style="font-weight:800;font-size:18px"></div>
    <div id="pVia" class="muted" style="font-size:13px"></div></div>
    <div id="pPremios" style="background:#F0F6FF;color:#1B7FE4;font-weight:700;font-size:12px;padding:6px 10px;border-radius:20px;white-space:nowrap"></div>
  </div>
  <div class="selos" id="selos"></div>
  <div id="zonaPremio"></div>
  <div id="zonaRegisto">
    <div id="okSemana"></div>
    <div class="tipos" id="tipos">
      <button data-t="exterior" class="on">exterior</button>
      <button data-t="interior">interior</button>
      <button data-t="completa">completa</button>
    </div>
    <button onclick="document.getElementById('foto').click()" id="btnReg">📷 Registar lavagem com foto</button>
    <input type="file" id="foto" accept="image/*" capture="environment" style="display:none">
  </div>
</div>

<div class="card" id="cardHist" style="display:none">
  <h1 style="font-size:15px">Histórico</h1>
  <div class="hist" id="hist"></div>
</div>

<script>
let motorista="", matricula="", tipo="exterior";
fetch("/api/lavagens/dados").then(r=>r.json()).then(d=>{
  const sm=document.getElementById("selMot"), sv=document.getElementById("selVia");
  d.motoristas.forEach(m=>sm.add(new Option(m,m)));
  d.viaturas.forEach(v=>sv.add(new Option(v.matricula+(v.modelo?" — "+v.modelo:""),v.matricula)));
  const par=new URLSearchParams(location.search), v=(par.get("v")||"").toUpperCase();
  if(v) sv.value=v;
  const mem=localStorage.getItem("movvi_motorista");
  if(mem && d.motoristas.includes(mem)) sm.value=mem;
  if(sv.value && sm.value) entrar();
});
document.getElementById("tipos").onclick=e=>{
  if(e.target.dataset.t){tipo=e.target.dataset.t;
    document.querySelectorAll("#tipos button").forEach(b=>b.classList.toggle("on",b===e.target));}
};
function toast(m){const t=document.createElement("div");t.className="toast";t.textContent=m;
  document.body.appendChild(t);setTimeout(()=>t.remove(),3200);}
function entrar(){
  motorista=document.getElementById("selMot").value;
  matricula=document.getElementById("selVia").value;
  if(!motorista||!matricula)return toast("Selecione motorista e viatura");
  localStorage.setItem("movvi_motorista",motorista);
  document.getElementById("login").style.display="none";
  document.getElementById("painel").style.display="block";
  document.getElementById("cardHist").style.display="block";
  document.getElementById("pNome").textContent=motorista;
  document.getElementById("pVia").textContent=matricula;
  atualizar();
}
function atualizar(){
  fetch("/api/lavagens/estado?motorista="+btoa(unescape(encodeURIComponent(motorista))))
    .then(r=>r.json()).then(render);
}
function render(e){
  document.getElementById("pPremios").textContent="🏆 "+e.premios+" incentivos";
  let s="";for(let i=0;i<5;i++)s+='<div class="selo'+(i<e.ciclo?" ok":"")+'">'+(i<e.ciclo?"✓":i+1)+'</div>';
  document.getElementById("selos").innerHTML=s;
  document.getElementById("zonaPremio").innerHTML=e.pendente?
    '<div class="premio">🎉 Incentivo desbloqueado!<br><span style="font-weight:400;font-size:13px">Prémio: 10 fichas de lavagem no Girassol 🎟️ Fale com a gestão para levantar.</span></div>':"";
  document.getElementById("zonaRegisto").style.display=e.pendente?"none":"block";
  document.getElementById("okSemana").innerHTML=e.semana?'<div class="ok-semana">✓ Lavagem desta semana registada</div>':"";
  document.getElementById("hist").innerHTML=e.historico.map(h=>
    '<div>'+(h.foto?'<img src="/lavagens/fotos/'+h.foto+'">':'')+
    '<div class="meta">'+h.data.slice(8,10)+"/"+h.data.slice(5,7)+' · '+h.tipo+'</div></div>').join("")
    || '<p class="muted">Ainda sem lavagens.</p>';
}
document.getElementById("foto").onchange=e=>{
  const f=e.target.files[0];if(!f)return;
  document.getElementById("btnReg").textContent="A guardar…";
  const img=new Image(),rd=new FileReader();
  rd.onload=ev=>{img.onload=()=>{
    const c=document.createElement("canvas"),sc=Math.min(1,600/img.width);
    c.width=img.width*sc;c.height=img.height*sc;
    c.getContext("2d").drawImage(img,0,0,c.width,c.height);
    fetch("/api/lavagens/registar",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({motorista,matricula,tipo,foto:c.toDataURL("image/jpeg",.7)})})
      .then(r=>r.json()).then(x=>{render(x);
        toast(x.pendente?"🎉 5 lavagens! Incentivo desbloqueado":"Lavagem registada ✓");
        document.getElementById("btnReg").textContent="📷 Registar lavagem com foto";});
  };img.src=ev.target.result;};
  rd.readAsDataURL(f);e.target.value="";
};
</script></body></html>"""

PAGINA_ADMIN = """<!DOCTYPE html><html lang="pt"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Movvi · Gestão de Lavagens</title><style>""" + _BASE_CSS + """</style></head><body>
<header><img src=\"""" + _LOGO + """\" alt="Movvi"><div class="sub">Gestão · Lavagens</div></header>
<div class="card">
  <h1>Frota — controlo semanal</h1>
  <div class="tipos" id="filtros">
    <button data-f="todos" class="on">Todos</button>
    <button data-f="semana">✓ Semana</button>
    <button data-f="falta">Em falta</button>
    <button data-f="premio">🏆</button>
  </div>
  <div id="lista"><p class="muted">A carregar…</p></div>
  <p class="muted" style="font-size:12px;margin-top:12px">● verde = lavou esta semana · vermelho = em falta</p>
</div>
<script>
let dados=[],filtro="todos";
const CHAVE="__CHAVE__";
function carregar(){fetch("/api/lavagens/frota?chave="+CHAVE).then(r=>r.json()).then(d=>{dados=d;render();});}
document.getElementById("filtros").onclick=e=>{
  if(e.target.dataset.f){filtro=e.target.dataset.f;
    document.querySelectorAll("#filtros button").forEach(b=>b.classList.toggle("on",b===e.target));render();}
};
function render(){
  const v=dados.filter(d=>filtro==="todos"||(filtro==="semana"&&d.semana)||
    (filtro==="falta"&&!d.semana)||(filtro==="premio"&&d.pendente));
  document.getElementById("lista").innerHTML=v.map(d=>
    '<div class="linha"><div style="flex:1;min-width:0"><div class="nome">'+d.nome+'</div>'+
    '<div class="muted" style="font-size:11.5px">'+d.total+' lav. · 🏆 '+d.premios+'</div></div>'+
    '<div class="dot" style="background:'+(d.semana?"#17B26A":"#E5484D")+'"></div>'+
    '<div class="cnt">'+(d.pendente?"5/5 🎉":d.ciclo+"/5")+'</div>'+
    (d.pendente?'<button style="width:auto;padding:7px 12px;font-size:12px;background:#17B26A" onclick="resgatar(\\''+d.nome.replace(/'/g,"\\\\'")+'\\')">Resgatar</button>':"")+
    '</div>').join("")||'<p class="muted">Sem motoristas neste filtro.</p>';
}
function resgatar(nome){
  fetch("/api/lavagens/resgatar",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({motorista:nome,chave:CHAVE})}).then(()=>carregar());
}
carregar();setInterval(carregar,60000);
</script></body></html>"""
