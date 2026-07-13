#!/usr/bin/env python3
"""
RENOVAR COOKIES UBER
Uso: python renovar_uber.py

Cole o curl copiado do browser quando pedido.
O script extrai os cookies e actualiza o config.py automaticamente.
"""

import re
import os
import subprocess
import sys

CONFIG_PATH = "/root/bolt_alertas/config.py"

def extrair_cookies(curl_text):
    """Extrai o valor dos cookies do curl copiado do browser."""
    # Procura o bloco -b "..." ou --cookie "..."
    match = re.search(r'(?:-b|--cookie)\s+["\^](.+?)["\^]\s+-H', curl_text, re.DOTALL)
    if not match:
        # Tenta formato alternativo
        match = re.search(r'-b\s+"([^"]+)"', curl_text, re.DOTALL)
    if not match:
        return None
    
    cookies_raw = match.group(1)
    # Limpa caracteres de escape do Windows (^)
    cookies_raw = cookies_raw.replace("^%^", "%").replace("^\^", "").replace("^{", "{").replace("^}", "}").replace("^[", "[").replace("^]", "]")
    cookies_raw = cookies_raw.replace("^\"", "\"").replace("^\n", "")
    # Remove quebras de linha e espaços extra
    cookies_raw = " ".join(cookies_raw.split())
    return cookies_raw

def actualizar_config(cookies):
    """Substitui UBER_COOKIES no config.py."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        conteudo = f.read()
    
    # Substitui a linha UBER_COOKIES
    novo = re.sub(
        r'UBER_COOKIES\s*=\s*"[^"]*"',
        f'UBER_COOKIES    = "{cookies}"',
        conteudo
    )
    
    if novo == conteudo:
        print("❌ Linha UBER_COOKIES não encontrada no config.py")
        return False
    
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(novo)
    
    print("✅ config.py actualizado com novos cookies!")
    return True

def reiniciar_sistema():
    """Para o processo actual e arranca um novo."""
    print("\nA reiniciar o sistema...")
    
    # Mata o processo actual
    result = subprocess.run(
        ["pkill", "-f", "main.py"],
        capture_output=True
    )
    
    import time
    time.sleep(2)
    
    # Arranca novo processo
    subprocess.Popen(
        ["python", "main.py"],
        cwd="/root/bolt_alertas",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
    time.sleep(3)
    
    # Confirma que está a correr
    result = subprocess.run(
        ["pgrep", "-f", "main.py"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        print(f"✅ Sistema reiniciado com PID: {result.stdout.strip()}")
    else:
        print("❌ Sistema não arrancou — corre manualmente: nohup python main.py > /dev/null 2>&1 &")

def main():
    print("=" * 55)
    print("  RENOVAR COOKIES UBER")
    print("=" * 55)
    print()
    print("1. Abre o Chrome com o portal Uber (supplier.uber.com)")
    print("2. Abre DevTools (F12) → Network")
    print("3. Clica num pedido /graphql → Copy as cURL")
    print("4. Cola aqui e prime ENTER duas vezes quando terminar")
    print()
    print("Cole o curl agora:")
    print("-" * 55)
    
    linhas = []
    try:
        while True:
            linha = input()
            if linha == "" and linhas and linhas[-1] == "":
                break
            linhas.append(linha)
    except EOFError:
        pass
    
    curl_text = "\n".join(linhas)
    
    if not curl_text.strip():
        print("❌ Nada foi colado.")
        sys.exit(1)
    
    print("\nA extrair cookies...")
    cookies = extrair_cookies(curl_text)
    
    if not cookies:
        print("❌ Não foi possível extrair os cookies do curl.")
        print("   Verifica se copiaste correctamente (Copy as cURL no Network tab)")
        sys.exit(1)
    
    # Mostra preview dos cookies principais
    jwt_match = re.search(r'jwt-session=([^;]+)', cookies)
    sid_match = re.search(r'sid=([^;]+)', cookies)
    
    if jwt_match:
        jwt = jwt_match.group(1)
        try:
            import base64, json
            payload = json.loads(base64.b64decode(jwt.split('.')[1] + '=='))
            from datetime import datetime
            exp = datetime.fromtimestamp(payload['exp'])
            horas = round((payload['exp'] - datetime.now().timestamp()) / 3600)
            print(f"   jwt-session expira: {exp.strftime('%d/%m %H:%M')} (em {horas}h)")
        except Exception:
            print(f"   jwt-session: {jwt[:30]}...")
    
    if sid_match:
        print(f"   sid: {sid_match.group(1)[:30]}...")
    
    print()
    actualizar = actualizar_config(cookies)
    
    if actualizar:
        reiniciar = input("\nReiniciar o sistema agora? (s/n): ").strip().lower()
        if reiniciar == "s":
            reiniciar_sistema()
        else:
            print("\nReinicia manualmente:")
            print("  pkill -f main.py && nohup python main.py > /dev/null 2>&1 & echo PID: $!")
    
    print("\nPronto!")

if __name__ == "__main__":
    main()
