"""
TVDE - Login automatico Bolt
1. Tenta refresh_token (rapido, sem browser)
2. Se falhar, usa Playwright para fazer login e captura novo refresh_token
"""
import re, os, sys, requests, importlib
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
BOLT_API    = "https://fleetownerportal.live.boltsvc.net/fleetOwnerPortal"
COMPANY_ID  = 19252

def read_config(key):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        content = f.read()
    m = re.search(key + r'\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else None

def write_config(key, value):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        content = f.read()
    pattern = key + r'\s*=\s*"[^"]*"'
    new_line = f'{key} = "{value}"'
    if re.search(pattern, content):
        content = re.sub(pattern, new_line, content)
    else:
        content = content.rstrip() + f'\n{new_line}\n'
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)

def refresh_token_login(refresh_token):
    try:
        r = requests.post(
            f"{BOLT_API}/getAccessToken",
            params={"language": "pt-pt", "version": "FO.3.1986", "brand": "bolt"},
            json={
                "refresh_token": refresh_token,
                "company": {"company_id": COMPANY_ID, "company_type": "fleet_company"}
            },
            headers={
                "Content-Type": "application/json",
                "Origin": "https://fleets.bolt.eu",
                "Referer": "https://fleets.bolt.eu/"
            },
            timeout=15
        )
        data = r.json()
        if data.get("code") == 0:
            access_token = data.get("data", {}).get("access_token", "")
            new_refresh  = data.get("data", {}).get("refresh_token", "")
            if access_token:
                write_config("BOLT_JWT_TOKEN", access_token)
                print(f"   Token JWT actualizado")
            if new_refresh:
                write_config("BOLT_REFRESH_TOKEN", new_refresh)
                print(f"   Refresh token renovado")
            return True if access_token else False
        else:
            print(f"   Refresh token invalido: {data.get('message')}")
            return False
    except Exception as e:
        print(f"   Erro refresh: {e}")
        return False

def playwright_login():
    try:
        from playwright.sync_api import sync_playwright

        email    = read_config("BOLT_EMAIL")
        password = read_config("BOLT_PASSWORD")

        if not email or not password:
            print("❌ BOLT_EMAIL ou BOLT_PASSWORD nao encontrados no config.py")
            return False

        print("Bolt: a tentar reutilizar sessao...")
        token_found   = None
        refresh_found = None

        import os
        STORAGE_PATH = "/root/bolt_alertas/bolt_session.json"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Usar sessao guardada se existir
            if os.path.exists(STORAGE_PATH):
                context = browser.new_context(storage_state=STORAGE_PATH)
            else:
                context = browser.new_context()
            page    = context.new_page()

            def handle_request(request):
                nonlocal token_found
                if "fleetownerportal.live.boltsvc.net" not in request.url:
                    return
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer eyJ"):
                    candidate = auth[7:].strip()
                    # Preferir token "company" que funciona para getLayers
                    try:
                        import base64 as b64, json as js
                        parts = candidate.split(".")
                        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        d = js.loads(b64.b64decode(payload))
                        tipo = d.get("data", {}).get("type", "")
                        if tipo == "company":
                            token_found = candidate
                            return
                    except:
                        pass
                    if not token_found:
                        token_found = candidate

            def handle_response(response):
                nonlocal refresh_found
                try:
                    if "getAccessToken" in response.url:
                        data = response.json()
                        rt = data.get("data", {}).get("refresh_token")
                        if rt:
                            refresh_found = rt
                except: pass

            page.on("request",  handle_request)
            page.on("response", handle_response)

            session_ok = False
            if os.path.exists(STORAGE_PATH):
                try:
                    page.goto(
                        "https://fleets.bolt.eu/19252/operations/track/liveMap?tab=online",
                        wait_until="domcontentloaded", timeout=40000
                    )
                    page.wait_for_timeout(3000)
                    current_url = page.url.lower()
                    print(f"   URL atual: {page.url}")
                    if not any(x in current_url for x in ["login", "signin", "auth"]):
                        session_ok = True
                        print("   Sessao reutilizada com sucesso!")
                    else:
                        print("   Sessao expirada, a fazer login...")
                except Exception as e:
                    print(f"   Erro ao testar sessao: {e}")

            if not session_ok:
                print(f"   Sessao expirada, login necessario ({email})")
                try:
                    page.goto("https://fleets.bolt.eu/login", wait_until="domcontentloaded", timeout=40000)
                    try: page.click("text=Allow all", timeout=3000)
                    except: pass
                    try: page.click("text=Permitir todos", timeout=2000)
                    except: pass
                    page.fill('input[name="email"]', email, timeout=10000)
                    page.wait_for_timeout(500)
                    page.fill('input[name="password"]', password, timeout=10000)
                    page.wait_for_timeout(500)
                    page.click('button[type="submit"]', timeout=10000)
                    page.wait_for_url("**/19252/**", timeout=20000)
                    page.wait_for_timeout(3000)
                    if not token_found:
                        page.goto("https://fleets.bolt.eu/19252/finances/reports/driverEarnings",
                                  wait_until="domcontentloaded", timeout=40000)
                        page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"   Aviso: {e}")

            # Guardar sessao para proximos logins
            try:
                context.storage_state(path=STORAGE_PATH)
                # Backup da sessao
                import shutil
                shutil.copy(STORAGE_PATH, STORAGE_PATH + ".bak")
                print(f"   Sessao guardada em {STORAGE_PATH}")
            except Exception as e:
                print(f"   Aviso ao guardar sessao: {e}")
            browser.close()

        if token_found:
            write_config("BOLT_JWT_TOKEN", token_found)
            print(f"   Token JWT actualizado")

        if refresh_found:
            write_config("BOLT_REFRESH_TOKEN", refresh_found)
            print(f"   Refresh token guardado (valido 7 dias)")
        elif token_found:
            try:
                r = requests.post(
                    f"{BOLT_API}/getAccessToken",
                    params={"language": "pt-pt", "version": "FO.3.1986", "brand": "bolt"},
                    json={"company": {"company_id": COMPANY_ID, "company_type": "fleet_company"}},
                    headers={
                        "Authorization": f"Bearer {token_found}",
                        "Content-Type": "application/json",
                        "Origin": "https://fleets.bolt.eu",
                        "Referer": "https://fleets.bolt.eu/"
                    },
                    timeout=15
                )
                data = r.json()
                print(f"   getAccessToken resposta: code={data.get('code')} keys={list(data.get('data',{}).keys())}")
                rt = data.get("data", {}).get("refresh_token")
                at = data.get("data", {}).get("access_token")
                if rt:
                    write_config("BOLT_REFRESH_TOKEN", rt)
                    print(f"   Refresh token obtido (valido 7 dias)")
                if at:
                    write_config("BOLT_JWT_TOKEN", at)
                    print(f"   JWT actualizado via getAccessToken")
            except Exception as e:
                print(f"   Nao foi possivel obter refresh_token: {e}")

        if token_found:
            print("✅ Login Bolt OK (via browser)")
            return True
        else:
            print("❌ Token nao encontrado")
            return False

    except Exception as e:
        print(f"❌ Playwright falhou: {e}")
        return False

def auto_login():
    refresh_token = read_config("BOLT_REFRESH_TOKEN")
    if refresh_token:
        print(f"Bolt: a renovar token via refresh_token...")
        if refresh_token_login(refresh_token):
            print("✅ Login Bolt OK (via refresh_token)")
            return True
        print(f"   A tentar Playwright...")
    return playwright_login()

if __name__ == "__main__":
    success = auto_login()
    sys.exit(0 if success else 1)
