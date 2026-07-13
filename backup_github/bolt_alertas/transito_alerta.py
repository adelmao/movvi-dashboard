#!/usr/bin/env python3
"""
transito_alerta.py — Movvi
Alertas de acidentes e vias fechadas via HERE Traffic API + WhatsApp Meta API
"""
import time, logging, requests, math
from datetime import datetime
from whatsapp_meta import enviar_whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRANSITO] %(message)s")
log = logging.getLogger(__name__)

HERE_API_KEY = "giq-tMlwBkrCxXdYYRP1ShZF-AW-X4quECukNoxjDBQ"
INTERVALO_VERIFICACAO = 300
COOLDOWN_MINUTOS = 120
_ultimo_alerta = {}
_alertados_ids = {}

ZONAS = [
    {"nome": "VCI / Via de Cintura Interna",    "bbox": "-8.680,41.140,-8.580,41.190"},
    {"nome": "A1 / Entrada Porto Sul",          "bbox": "-8.640,41.095,-8.570,41.140"},
    {"nome": "A3 / Entrada Porto Norte",        "bbox": "-8.640,41.185,-8.570,41.230"},
    {"nome": "A28 / Matosinhos - Exponor",      "bbox": "-8.720,41.175,-8.655,41.215"},
    {"nome": "IP4 / Entrada Este (Gondomar)",   "bbox": "-8.560,41.140,-8.490,41.180"},
    {"nome": "Baixa / Ribeira / Aliados",       "bbox": "-8.625,41.140,-8.600,41.158"},
    {"nome": "Boavista / Casa da Musica",       "bbox": "-8.645,41.155,-8.615,41.170"},
    {"nome": "Aeroporto Francisco Sa Carneiro", "bbox": "-8.705,41.225,-8.670,41.250"},
    {"nome": "Gaia / Arrabida / A1",            "bbox": "-8.640,41.095,-8.590,41.135"},
    {"nome": "Matosinhos / Leca",               "bbox": "-8.715,41.180,-8.660,41.215"},
    {"nome": "Gondomar / EN108",                "bbox": "-8.560,41.135,-8.490,41.175"},
    {"nome": "Maia / A41 (CRIP)",               "bbox": "-8.640,41.220,-8.570,41.265"},
]

TIPOS_ALERTA = {"accident", "roadClosure", "roadHazard", "disabledVehicle"}

def calcular_sentido(pts):
    if len(pts) < 2:
        return ""
    lat1, lng1 = pts[0]["lat"], pts[0]["lng"]
    lat2, lng2 = pts[-1]["lat"], pts[-1]["lng"]
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    if abs(dlat) > abs(dlng):
        return "Sul -> Norte" if dlat > 0 else "Norte -> Sul"
    else:
        return "Oeste -> Este" if dlng > 0 else "Este -> Oeste"

def reverse_geocode(lat, lng):
    try:
        r = requests.get(
            f"https://revgeocode.search.hereapi.com/v1/revgeocode?at={lat},{lng}&lang=pt-PT&apiKey={HERE_API_KEY}",
            timeout=5
        )
        items = r.json().get("items", [])
        if items:
            addr = items[0].get("address", {})
            rua = addr.get("street", "")
            distrito = addr.get("district", "")
            return f"{rua}, {distrito}" if rua and distrito else rua or distrito or ""
    except:
        pass
    return f"{lat:.4f},{lng:.4f}"

def buscar_incidentes(bbox):
    url = f"https://data.traffic.hereapi.com/v7/incidents?locationReferencing=shape&in=bbox:{bbox}&apiKey={HERE_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.warning(f"Erro HERE ({bbox}): {e}")
        return []

def filtrar_incidentes(incidentes):
    relevantes = []
    for inc in incidentes:
        det = inc.get("incidentDetails", {})
        tipo = det.get("type", "")
        closed = det.get("roadClosed", False)
        inc_id = det.get("id", "")
        if (tipo in TIPOS_ALERTA or closed) and inc_id not in _alertados_ids or (datetime.now() - _alertados_ids.get(inc_id, datetime.min)).total_seconds() > 21600:
            relevantes.append(inc)
    return relevantes

def formatar_mensagem(zona, incidentes):
    hora = datetime.now().strftime("%H:%M")
    linhas = []
    for inc in incidentes[:5]:
        det = inc.get("incidentDetails", {})
        loc = inc.get("location", {})
        tipo = det.get("type", "")
        closed = det.get("roadClosed", False)
        desc = det.get("description", {}).get("value", "")
        summary = det.get("summary", {}).get("value", "")
        
        pts = []
        links = loc.get("shape", {}).get("links", [])
        if links:
            pts = links[0].get("points", [])
        
        sentido = calcular_sentido(pts)
        local = ""
        if pts:
            local = reverse_geocode(pts[0]["lat"], pts[0]["lng"])
        
        emoji = "🆘" if closed else "🚨"
        tipo_pt = {"accident":"Acidente","roadClosure":"Via Fechada","roadHazard":"Perigo na via","disabledVehicle":"Viatura avariada"}.get(tipo, tipo)
        
        linha = f"{emoji} {tipo_pt}"
        if local:
            linha += f"\n   Local: {local}"
        if sentido:
            linha += f"\n   Sentido: {sentido}"
        if desc and desc != summary:
            linha += f"\n   {desc}"
        linhas.append(linha)
    
    n_closed = sum(1 for i in incidentes if i.get("incidentDetails",{}).get("roadClosed"))
    header_emoji = "🆘" if n_closed > 0 else "🚨"
    header_label = "VIA FECHADA" if n_closed > 0 else "ALERTA DE TRANSITO"
    
    return (
        f"{header_emoji} *MOVVI - {header_label}*\n"
        f"------------------------\n"
        f"Zona: {zona}\n"
        f"Hora: {hora}\n"
        f"Ocorrencias: {len(incidentes)}\n\n"
        + "\n\n".join(linhas) + "\n\n"
        f"Evita esta zona se possivel.\n"
        f"_Movvi Fleet - Alerta automatico_"
    )

def em_cooldown(zona):
    ultimo = _ultimo_alerta.get(zona)
    if not ultimo:
        return False
    return (datetime.now() - ultimo).total_seconds() / 60 < COOLDOWN_MINUTOS

def verificar_transito():
    log.info("A verificar transito em todas as zonas...")
    for zona in ZONAS:
        nome = zona["nome"]
        if em_cooldown(nome):
            log.info(f"  {nome} - cooldown activo")
            continue
        incidentes = buscar_incidentes(zona["bbox"])
        relevantes = filtrar_incidentes(incidentes)
        n_total = len(incidentes)
        n_rel = len(relevantes)
        log.info(f"  {nome} - {n_total} total, {n_rel} relevante(s)")
        if relevantes:
            mensagem = formatar_mensagem(nome, relevantes)
            log.info(f"  A enviar alerta para {nome}...")
            enviar_whatsapp(mensagem)
            _ultimo_alerta[nome] = datetime.now()
            for inc in relevantes:
                _alertados_ids[inc.get("incidentDetails",{}).get("id","")] = datetime.now()

    log.info("Verificacao concluida.")

if __name__ == "__main__":
    print("=" * 50)
    print("  MOVVI - Alerta de Transito (HERE)")
    print("=" * 50)
    while True:
        verificar_transito()
        print(f"\nProxima verificacao em {INTERVALO_VERIFICACAO // 60} minutos...\n")
        time.sleep(INTERVALO_VERIFICACAO)
