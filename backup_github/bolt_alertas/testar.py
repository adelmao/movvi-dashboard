#!/usr/bin/env python3
# =============================================================
#  BOLT ALERTAS - TESTE DE CONFIGURAÇÃO
#  Corre este ficheiro primeiro para verificar que tudo está OK
#  antes de arrancar o sistema principal
# =============================================================

import sys

def verificar_dependencias():
    print("\n1. A verificar dependências Python...")
    pacotes = {
        "requests":   "requests",
        "playwright": "playwright",
        "twilio":     "twilio",
        "h3":         "h3",
    }
    em_falta = []
    for nome, pacote in pacotes.items():
        try:
            __import__(nome)
            print(f"   ✓ {pacote}")
        except ImportError:
            print(f"   ✗ {pacote} — em falta")
            em_falta.append(pacote)

    if em_falta:
        print(f"\n   Instala com:")
        print(f"   pip install {' '.join(em_falta)}")
        if "playwright" in em_falta:
            print(f"   playwright install chromium")
        return False
    return True


def verificar_config():
    print("\n2. A verificar configuração...")
    from config import (
        BOLT_EMAIL, BOLT_PASSWORD, BOLT_COMPANY_ID,
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM,
        MOTORISTAS
    )

    ok = True
    if "SEU_EMAIL" in BOLT_EMAIL:
        print("   ✗ BOLT_EMAIL não configurado em config.py")
        ok = False
    else:
        print(f"   ✓ Bolt email: {BOLT_EMAIL}")

    if "SEU_EMAIL" in BOLT_PASSWORD or len(BOLT_PASSWORD) < 4:
        print("   ✗ BOLT_PASSWORD não configurada em config.py")
        ok = False
    else:
        print(f"   ✓ Bolt password: {'*' * len(BOLT_PASSWORD)}")

    if "ACxxx" in TWILIO_ACCOUNT_SID:
        print("   ✗ TWILIO_ACCOUNT_SID não configurado em config.py")
        ok = False
    else:
        print(f"   ✓ Twilio SID: {TWILIO_ACCOUNT_SID[:8]}...")

    print(f"   ✓ {len(MOTORISTAS)} motoristas configurados")
    return ok


def testar_bolt():
    print("\n3. A testar ligação à Bolt Fleet API...")
    from main import SessaoBolt, obter_layers, parse_snapshot
    try:
        sessao = SessaoBolt()
        dados = obter_layers(sessao)
        if dados and dados.get("code") == 0:
            snapshot = parse_snapshot(dados)
            cells_demand = len([s for s in snapshot.values() if s <= 5])
            cells_surge  = len([s for s in snapshot.values() if s > 5])
            score_max    = max(snapshot.values()) if snapshot else 0
            print(f"   ✓ API responde! {cells_demand} células demand, {cells_surge} com surge")
            print(f"   ✓ Score máximo actual: {min(score_max, 5)}/5")

            # Mostra top 5 zonas
            from zonas_porto import nome_zona
            top = sorted(snapshot.items(), key=lambda x: x[1], reverse=True)[:5]
            print(f"\n   Top 5 zonas agora:")
            for h3, score in top:
                print(f"     {min(score,5)}/5  {nome_zona(h3)}")
            return True
        else:
            print(f"   ✗ Resposta inesperada: {dados}")
            return False
    except Exception as e:
        print(f"   ✗ Erro: {e}")
        return False


def testar_whatsapp():
    print("\n4. A testar WhatsApp (envio de teste para o 1º motorista)...")
    from config import MOTORISTAS, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
    from twilio.rest import Client

    if not MOTORISTAS:
        print("   ✗ Nenhum motorista configurado")
        return False

    nome, numero = next(iter(MOTORISTAS.items()))
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            body="✅ Bolt Alertas configurado com sucesso! Este é o teste de ligação.",
            from_=TWILIO_FROM,
            to=numero
        )
        print(f"   ✓ Mensagem de teste enviada para {nome} ({numero})")
        print(f"   ✓ Message SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"   ✗ Erro Twilio: {e}")
        print(f"   Verifica TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e TWILIO_FROM em config.py")
        return False


if __name__ == "__main__":
    print("=" * 55)
    print("  BOLT ALERTAS — VERIFICAÇÃO DE CONFIGURAÇÃO")
    print("=" * 55)

    dep_ok  = verificar_dependencias()
    if not dep_ok:
        print("\n❌ Instala as dependências em falta antes de continuar.\n")
        sys.exit(1)

    conf_ok = verificar_config()
    if not conf_ok:
        print("\n❌ Edita config.py com os teus dados antes de continuar.\n")
        sys.exit(1)

    bolt_ok = testar_bolt()
    wa_ok   = testar_whatsapp()

    print("\n" + "=" * 55)
    if bolt_ok and wa_ok:
        print("  ✅ TUDO OK! Para arrancar o sistema:")
        print("     python main.py")
    else:
        print("  ⚠️  Alguns testes falharam. Revê os erros acima.")
    print("=" * 55 + "\n")
