#!/usr/bin/env python3
import time, json, logging, requests
from datetime import datetime, timedelta
from config import *
from transito_alerta import verificar_transito
from tempo_previsao import agendar_previsao_tempo
import schedule
from zonas_porto import nome_zona
from uber_api import obter_snapshot_uber

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", handlers=[logging.FileHandler("alertas.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger(__name__)

COR_PARA_SCORE = {"#E6CCFF8C":1,"#C084FC8C":2,"#9B4DFF8C":3,"#6F1BFF8C":4,"#5400EF8C":5}
SURGE_BONUS = 2

# --- Meta API ---
META_TOKEN    = "EAAVHi2m5MZCkBRh80CqRbemqJKQ7ZCYty4HNubhPvets09vTtkZA6tUzWU6LOdW8xmOZBGxhHFUe9kSCZCSFG6N61MKl8IBc1HEyJ0vRtjKAZBlnZBhU6MapdtZAkcJm4zNWkuKdATlk7yZCyDeWs8TnQukAfmEKEM0ZBF4RmW2LDwGtoKDzXT9tKJaMWAKZBS9E56uhQZDZD"
META_PHONE_ID = "1135522376308599"
MOTORISTAS    = {"Adelmo": "351913606800"}

class SessaoBolt:
    def get_token(self):
        import importlib, config as cfg
        importlib.reload(cfg)
        return cfg.BOLT_JWT_TOKEN

    def renovar_token(self):
        agora = datetime.now()
        if hasattr(self, '_ultimo_renovar') and (agora - self._ultimo_renovar).total_seconds() < 840:
            return  # Nao renovar mais que uma vez a cada 14 minutos
        log.info("Token Bolt expirado - a renovar via Playwright...")
        from bolt_auto_login import auto_login
        if auto_login():
            log.info("Token Bolt renovado com sucesso.")
            self._ultimo_renovar = agora
        else:
            log.error("Renovacao de token Bolt falhou.")

    def obter_headers(self):
        token = self.get_token()
        if not token:
            self.renovar_token()
            token = self.get_token()
        return {"accept":"application/json","content-type":"application/json","authorization":f"Bearer {token}","origin":"https://fleets.bolt.eu","referer":f"https://fleets.bolt.eu/{BOLT_COMPANY_ID}/operations/track/liveMap?tab=online","user-agent":"Mozilla/5.0"}

def obter_layers(sessao):
    url = f"https://fleetownerportal.live.boltsvc.net/fleetOwnerPortal/liveMap/getLayers?language={BOLT_LANGUAGE}&version={BOLT_VERSION}&company_id={BOLT_COMPANY_ID}&user_id={BOLT_USER_ID}&brand=bolt"
    try:
        resp = requests.post(url, headers=sessao.obter_headers(), json={"layer_ids":["demand","surge"]}, timeout=10)
        resp.raise_for_status()
        dados = resp.json()
        code = dados.get("code")
        msg = dados.get("message", "")
        if code in (999, 401, 503) or "NOT_AUTHORIZED" in msg or "UNAUTHORIZED" in msg:
            log.info("Token Bolt expirado (503/NOT_AUTHORIZED) - a renovar...")
            sessao.renovar_token()
            resp = requests.post(url, headers=sessao.obter_headers(), json={"layer_ids":["demand","surge"]}, timeout=10)
            resp.raise_for_status()
            dados = resp.json()
        return dados
    except Exception as e:
        log.error(f"Erro getLayers: {e}")
        return None

def parse_snapshot(dados):
    scores = {}
    surge_indices = set()
    for layer in dados.get("data",{}).get("layers",[]):
        if layer.get("id") == "surge":
            surge_indices = {c["h3_index"] for c in layer.get("cells",[])}
        if layer.get("id") == "demand":
            for cell in layer.get("cells",[]):
                scores[cell["h3_index"]] = COR_PARA_SCORE.get(cell["fill_color"].upper(), 1)
    for h3 in surge_indices:
        scores[h3] = min(scores.get(h3, 0) + SURGE_BONUS, 7)
    return scores

def detectar_aumentos(anterior, actual):
    alertas = []
    for h3, score_actual in actual.items():
        score_anterior = anterior.get(h3, 0)
        aumento = score_actual - score_anterior
        if aumento >= AUMENTO_MINIMO and score_actual >= SCORE_MINIMO_ALERTA:
            alertas.append({"h3":h3,"score_anterior":score_anterior,"score_actual":score_actual,"aumento":aumento,"nome":nome_zona(h3),"surge":score_actual>5})
    alertas.sort(key=lambda x: x["score_actual"], reverse=True)
    return alertas[:ZONAS_SIMULTANEAS_MAX]

def enviar_whatsapp_meta(numero, hora, zona_nome, plataformas):
    url = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": "alerta_procura_tvde",
            "language": {"code": "pt_PT"},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "parameter_name": "hora", "text": hora},
                {"type": "text", "parameter_name": "zona", "text": zona_nome},
                {"type": "text", "parameter_name": "plataformas", "text": plataformas}
            ]}]
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Erro Meta API ({numero}): {e} | {resp.text if 'resp' in dir() else ''}")
        return False

class GestorAlertas:
    def __init__(self):
        self.cooldowns = {}
        self.total_enviados = 0
    def em_cooldown(self, h3):
        return h3 in self.cooldowns and datetime.now() - self.cooldowns[h3] < timedelta(minutes=COOLDOWN_MINUTOS)
    def montar_mensagem(self, zona):
        from datetime import timezone, timedelta
        lisboa = timezone(timedelta(hours=1))
        hora = datetime.now(lisboa).strftime("%H:%M")
        uber_eph = zona.get("uber_eph", 0)
        if uber_eph >= 20:
            foguinhos = "🚨" * min(zona.get("score_actual", 4), 5)
        elif uber_eph >= 18:
            foguinhos = "🚀" * min(zona.get("score_actual", 4), 5)
        else:
            foguinhos = "🔥" * min(zona.get("score_actual", 4), 5)
        surge_txt = "\n⚡ *TARIFA DINAMICA NA ZONA*" if zona.get("surge") else ""
        bolt_txt = f"🔵 *BOLT* {foguinhos}" if zona.get("bolt") else ""
        uber_txt = f"🚗 *UBER* €{zona['uber_eph']:.2f}/hora" if zona.get("uber") else ""
        plataformas = "\n".join(filter(None, [bolt_txt, uber_txt]))
        return f"🔥 *ALERTA DE PROCURA* ({hora})\n\n📍 *{zona['nome']}*\n{plataformas}{surge_txt}\n\n🚀 Movimento forte nesta zona agora!"
    def enviar_alerta(self, zona):
        if self.em_cooldown(zona["h3"]):
            return
        enviados = 0
        from datetime import timezone, timedelta
        lisboa = timezone(timedelta(hours=1))
        hora = datetime.now(lisboa).strftime("%H:%M")
        uber_eph = zona.get("uber_eph", 0)
        if uber_eph >= 20:
            foguinhos = "🚨" * min(zona.get("score_actual", 4), 5)
        elif uber_eph >= 18:
            foguinhos = "🚀" * min(zona.get("score_actual", 4), 5)
        else:
            foguinhos = "🔥" * min(zona.get("score_actual", 4), 5)
        bolt_txt = f"🔵 BOLT {foguinhos}" if zona.get("bolt") else ""
        uber_txt = f"🚗 UBER {uber_eph:.2f}€/hora" if zona.get("uber") else ""
        plataformas_txt = " | ".join(filter(None, [bolt_txt, uber_txt]))
        for nome, numero in MOTORISTAS.items():
            if enviar_whatsapp_meta(numero, hora, zona["nome"], plataformas_txt):
                enviados += 1
                log.info(f"✅ Enviado para {nome} ({numero})")
            else:
                log.error(f"❌ Falhou para {nome} ({numero})")
        self.cooldowns[zona["h3"]] = datetime.now()
        self.total_enviados += enviados
        log.info(f"Alerta: {zona['nome']} (Bolt:{zona.get('bolt',False)} Uber:{zona.get('uber',False)}) -> {enviados} motoristas")
    def processar_alertas(self, zonas):
        for zona in zonas:
            self.enviar_alerta(zona)

def main():
    log.info("="*60)
    log.info("  BOLT + UBER ALERTAS v4 - META API DIRETA")
    log.info(f"  Motoristas: {len(MOTORISTAS)} | Intervalo: {INTERVALO_SEGUNDOS}s")
    log.info("="*60)
    sessao = SessaoBolt()
    gestor = GestorAlertas()
    snapshot_bolt_anterior = {}
    snapshot_uber_anterior = {}
    agendar_previsao_tempo()
    ciclos = 0
    _ultimo_transito = datetime.now() - timedelta(minutes=6)
    ultimo_login = datetime.now()
    while True:
        ciclos += 1
        inicio = time.time()
        # Renovar token proativamente a cada 2 horas
        if (datetime.now() - ultimo_login).total_seconds() >= 7200:
            try:
                log.info("Renovacao proativa do token Bolt (2h)...")
                auto_login()
            except Exception as e:
                log.error(f"Renovacao proativa falhou: {e}")
            ultimo_login = datetime.now()
        try:
            dados_bolt = obter_layers(sessao)
            snapshot_bolt = parse_snapshot(dados_bolt) if dados_bolt and dados_bolt.get("code") == 0 else {}
            snapshot_uber = obter_snapshot_uber()

            if snapshot_bolt_anterior or snapshot_uber_anterior:
                alertas_bolt = detectar_aumentos(snapshot_bolt_anterior, snapshot_bolt) if snapshot_bolt else []
                zonas_finais = []
                h3_bolt = {a["h3"] for a in alertas_bolt}

                for alerta in alertas_bolt:
                    h3 = alerta["h3"]
                    uber_eph = snapshot_uber.get(h3, 0)
                    alerta["bolt"] = True
                    alerta["uber"] = uber_eph >= UBER_EPH_MINIMO
                    alerta["uber_eph"] = uber_eph
                    if alerta["uber"]:
                        zonas_finais.append(alerta)

                for h3, eph in snapshot_uber.items():
                    if h3 not in h3_bolt and eph >= UBER_EPH_MINIMO * 1.5:
                        zonas_finais.append({
                            "h3": h3,
                            "nome": nome_zona(h3),
                            "score_actual": 4,
                            "score_anterior": 0,
                            "aumento": 4,
                            "surge": False,
                            "bolt": False,
                            "uber": True,
                            "uber_eph": eph,
                        })

                zonas_finais.sort(key=lambda x: x["score_actual"], reverse=True)
                zonas_finais = zonas_finais[:ZONAS_SIMULTANEAS_MAX]

                if zonas_finais:
                    log.info(f"Ciclo {ciclos}: {len(zonas_finais)} zona(s) detectadas")
                    gestor.processar_alertas(zonas_finais)
                else:
                    log.info(f"Ciclo {ciclos}: sem aumentos")
            else:
                log.info(f"Ciclo {ciclos}: primeiro snapshot (Bolt:{len(snapshot_bolt)} Uber:{len(snapshot_uber)})")

            snapshot_bolt_anterior = snapshot_bolt
            snapshot_uber_anterior = snapshot_uber

        except KeyboardInterrupt:
            log.info(f"A parar... Total: {gestor.total_enviados}")
            break
        except Exception as e:
            log.error(f"Ciclo {ciclos}: {e}")
        schedule.run_pending()
        if (datetime.now() - _ultimo_transito).total_seconds() >= 300:
            try:
                verificar_transito()
            except Exception as e:
                log.error(f"Erro transito: {e}")
            _ultimo_transito = datetime.now()
        time.sleep(max(0, INTERVALO_SEGUNDOS - (time.time() - inicio)))

if __name__ == "__main__":
    main()
