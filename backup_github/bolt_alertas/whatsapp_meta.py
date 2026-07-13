"""
whatsapp_meta.py — Módulo Movvi
Envio de mensagens WhatsApp via Meta API
Envia para o gestor (Adelmo) que reencaminha para a lista de transmissão
"""
import requests
import logging

try:
    from config import META_TOKEN, META_PHONE_ID, META_GESTOR_TEL
except ImportError:
    META_TOKEN      = ""
    META_PHONE_ID   = "1135522376308599"
    META_GESTOR_TEL = "351913606800"

def enviar_whatsapp(mensagem: str) -> bool:
    url = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": META_GESTOR_TEL,
        "type": "text",
        "text": {"body": mensagem},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        logging.info(f"✅ WhatsApp enviado para {META_GESTOR_TEL}")
        return True
    except Exception as e:
        logging.error(f"❌ Erro Meta API: {e} — {r.text if 'r' in dir() else ''}")
        return False
