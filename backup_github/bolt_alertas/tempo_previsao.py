"""
tempo_previsao.py — Módulo Movvi
Previsão do tempo para o dia seguinte, enviada às 21h via WhatsApp (Twilio)

Integra no sistema bolt_alertas existente em /root/bolt_alertas/
Adicionar ao main.py: from tempo_previsao import agendar_previsao_tempo

Dependências (já instaladas no venv):
  pip install requests twilio schedule

Fonte de dados: Open-Meteo API (100% gratuita, sem registo, sem chave)
  https://open-meteo.com
"""

import logging
import time
from datetime import datetime, timedelta

import requests
import schedule
from whatsapp_meta import enviar_whatsapp

# ─── Importa config do sistema existente ───────────────────────────────────
try:
    from config import (
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM,
        MOTORISTAS,
    )
except ImportError:
    TWILIO_ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    TWILIO_AUTH_TOKEN  = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    TWILIO_FROM        = "whatsapp:+14155238886"
    MOTORISTAS         = [
        {"nome": "Adelmo Filho",  "tel": "whatsapp:+351912345678"},
        {"nome": "Cris Veloso",   "tel": "whatsapp:+351923456789"},
    ]

# ─── Configuração ───────────────────────────────────────────────────────────

# Hora de envio (24h)
HORA_ENVIO = "21:00"

# Coordenadas — Porto
LATITUDE  = 41.1579
LONGITUDE = -8.6291
CIDADE    = "Porto"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TEMPO] %(message)s")
pass  # Meta API usada em substituição


# ─── Open-Meteo API (gratuita, sem chave) ───────────────────────────────────

WMO_CODES = {
    0:  ("☀️",  "Céu limpo"),
    1:  ("🌤️", "Principalmente limpo"),
    2:  ("⛅",  "Parcialmente nublado"),
    3:  ("🌥️", "Coberto"),
    45: ("🌫️", "Nevoeiro"),
    48: ("🌫️", "Nevoeiro com gelo"),
    51: ("🌦️", "Chuvisco leve"),
    53: ("🌦️", "Chuvisco moderado"),
    55: ("🌧️", "Chuvisco denso"),
    61: ("🌧️", "Chuva leve"),
    63: ("🌧️", "Chuva moderada"),
    65: ("🌧️", "Chuva forte"),
    71: ("🌨️", "Neve leve"),
    73: ("🌨️", "Neve moderada"),
    75: ("🌨️", "Neve forte"),
    80: ("🌦️", "Aguaceiros leves"),
    81: ("🌧️", "Aguaceiros moderados"),
    82: ("⛈️",  "Aguaceiros violentos"),
    95: ("⛈️",  "Trovoada"),
    96: ("⛈️",  "Trovoada com granizo"),
    99: ("⛈️",  "Trovoada forte com granizo"),
}


def buscar_previsao() -> dict | None:
    """Consulta Open-Meteo e retorna dados do dia seguinte."""
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        "&daily=weathercode,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,windspeed_10m_max,"
        "precipitation_sum,uv_index_max"
        "&timezone=Europe%2FLisbon"
        f"&start_date={amanha}&end_date={amanha}"
    )

    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})

        return {
            "data":        amanha,
            "wmo":         daily["weathercode"][0],
            "temp_max":    round(daily["temperature_2m_max"][0]),
            "temp_min":    round(daily["temperature_2m_min"][0]),
            "chuva_prob":  daily["precipitation_probability_max"][0],
            "chuva_mm":    round(daily["precipitation_sum"][0], 1),
            "vento":       round(daily["windspeed_10m_max"][0]),
            "uv":          daily["uv_index_max"][0],
        }
    except Exception as e:
        logging.error(f"Erro Open-Meteo: {e}")
        return None


# ─── Análise de impacto operacional ─────────────────────────────────────────

def calcular_impacto(previsao: dict) -> tuple[str, str, str]:
    """
    Retorna (nivel, emoji_nivel, dica_operacional).
    Nivel: BAIXO / MÉDIO / ALTO / CRÍTICO
    """
    wmo        = previsao["wmo"]
    chuva_prob = previsao["chuva_prob"]
    vento      = previsao["vento"]

    # Condições severas
    if wmo >= 80 or chuva_prob >= 80 or vento >= 50:
        nivel = "CRÍTICO"
        emoji = "🆘"
        if vento >= 50:
            dica = (
                "Vento muito forte previsto. Evita pontes e vias expostas (A2, Vasco da Gama). "
                "Procura elevada nas zonas urbanas — toda a frota disponível."
            )
        else:
            dica = (
                "Chuva intensa prevista — procura no pico. Posiciona a frota junto a paragens de metro, "
                "CP e aeroporto. Surge pricing muito provável. Segurança em 1º lugar."
            )

    elif wmo >= 61 or chuva_prob >= 50 or vento >= 35:
        nivel = "ALTO"
        emoji = "🔴"
        dica = (
            "Chuva moderada esperada. Reforça motoristas entre as 17h–20h na Baixa, Chiado e eixos principais. "
            "Procura acima do normal — aproveita para maximizar viagens."
        )

    elif wmo >= 51 or chuva_prob >= 30 or vento >= 25:
        nivel = "MÉDIO"
        emoji = "🟠"
        dica = (
            "Tempo instável. Ligeiro aumento de procura ao fim do dia. "
            "Mantém distribuição habitual mas fica atento às zonas de restauração ao jantar."
        )

    else:
        nivel = "BAIXO"
        emoji = "🟢"
        dica = (
            "Dia com boas condições. Procura estável e previsível. "
            "Foco nas zonas de lazer, praias urbanas e saídas nocturnas ao fim do dia."
        )

    return nivel, emoji, dica


# ─── Formatar mensagem WhatsApp ──────────────────────────────────────────────

DIAS_PT = {
    "Monday": "Segunda-feira", "Tuesday": "Terça-feira", "Wednesday": "Quarta-feira",
    "Thursday": "Quinta-feira", "Friday": "Sexta-feira", "Saturday": "Sábado", "Sunday": "Domingo",
}
MESES_PT = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def formatar_mensagem(previsao: dict) -> str:
    amanha_dt  = datetime.strptime(previsao["data"], "%Y-%m-%d")
    dia_semana = DIAS_PT.get(amanha_dt.strftime("%A"), amanha_dt.strftime("%A"))
    dia_num    = amanha_dt.day
    mes_nome   = MESES_PT[amanha_dt.month]

    emoji_tempo, descricao = WMO_CODES.get(previsao["wmo"], ("🌡️", "Condições variáveis"))
    nivel, emoji_nivel, dica = calcular_impacto(previsao)

    chuva_str = f"{previsao['chuva_mm']} mm" if previsao["chuva_mm"] > 0 else "0 mm"

    # Indicador visual de procura
    procura_bars = {"BAIXO": "▓░░░░", "MÉDIO": "▓▓▓░░", "ALTO": "▓▓▓▓░", "CRÍTICO": "▓▓▓▓▓"}
    barra = procura_bars.get(nivel, "░░░░░")

    return (
        f"🌤️ *PREVISÃO MOVVI — {dia_semana.upper()}*\n"
        f"📅 {dia_num} de {mes_nome} · {CIDADE}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji_tempo} *{descricao}*\n"
        f"🌡️ {previsao['temp_min']}° – {previsao['temp_max']}°C\n"
        f"🌧️ Chuva: {previsao['chuva_prob']}% ({chuva_str})\n"
        f"💨 Vento: {previsao['vento']} km/h\n"
        f"☀️ UV: {previsao['uv']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji_nivel} *IMPACTO OPERACIONAL: {nivel}*\n"
        f"Procura estimada: {barra}\n\n"
        f"💡 {dica}\n\n"
        f"_Movvi Fleet · Previsão automática às 21h_"
    )


# ─── Envio WhatsApp ──────────────────────────────────────────────────────────
def enviar_whatsapp(mensagem: str) -> None:
    from whatsapp_meta import enviar_whatsapp as _meta
    _meta(mensagem)


def executar_envio_diario() -> None:
    """Chamada pelo scheduler às 21h."""
    logging.info("🌤️ A preparar previsão do tempo para amanhã...")
    previsao = buscar_previsao()

    if not previsao:
        logging.error("Não foi possível obter previsão. Envio cancelado.")
        return

    mensagem = formatar_mensagem(previsao)
    logging.info(f"📤 A enviar para {len(MOTORISTAS)} motoristas...")
    enviar_whatsapp(mensagem)
    logging.info("✅ Previsão enviada com sucesso.")


# ─── Agendamento (integrar no main.py) ──────────────────────────────────────

def agendar_previsao_tempo() -> None:
    """
    Agenda o envio diário às 21h.
    Chamar UMA VEZ no arranque do main.py:

        from tempo_previsao import agendar_previsao_tempo
        agendar_previsao_tempo()

    O schedule.run_pending() já deve estar no loop principal do main.py.
    Se não estiver, adiciona:

        while True:
            schedule.run_pending()
            time.sleep(60)
    """
    schedule.every().day.at(HORA_ENVIO).do(executar_envio_diario)
    logging.info(f"📅 Previsão do tempo agendada para as {HORA_ENVIO} diariamente.")


# ─── Standalone (teste directo) ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  MOVVI — Teste de Previsão do Tempo")
    print("=" * 50)

    print("\n🔍 A consultar Open-Meteo (sem chave necessária)...\n")
    previsao = buscar_previsao()

    if previsao:
        print("📋 Dados recebidos:")
        for k, v in previsao.items():
            print(f"  {k}: {v}")
        print("\n📱 Mensagem que seria enviada:")
        print("-" * 50)
        print(formatar_mensagem(previsao))
        print("-" * 50)

        resposta = input("\nEnviar agora via WhatsApp? (s/n): ").strip().lower()
        if resposta == "s":
            enviar_whatsapp(formatar_mensagem(previsao))
            print("✅ Enviado!")
        else:
            print("ℹ️  Envio cancelado. Usa agendar_previsao_tempo() para envio automático às 21h.")
    else:
        print("❌ Não foi possível obter previsão. Verifica a ligação à internet.")

# Sobrepõe a função de envio para usar Meta API
from whatsapp_meta import enviar_whatsapp as _meta_send

def enviar_whatsapp(mensagem: str) -> None:
    _meta_send(mensagem)
