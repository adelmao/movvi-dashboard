#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOVVI TVDE FLEET — Sincronização de Motoristas (GesTVDE → SQLite)
==================================================================
Entra em movvi.com.pt/admin/drivers com Playwright, lê todos os
motoristas com o estado real (Ativo/Inativo) e atualiza a tabela
`motoristas` do sistema:
  - marca ativo=1/0 conforme o GesTVDE
  - insere motoristas novos automaticamente

Credenciais (NUNCA no crontab): criar /opt/tvde/.env_movvi com:
    export MOVVI_EMAIL="o-seu-email"
    export MOVVI_PASS="a-sua-password"
  e proteger:  chmod 600 /opt/tvde/.env_movvi

Teste manual:
    source /opt/tvde/.env_movvi && /opt/tvde/venv/bin/python sync_motoristas.py

Cron diário às 06:00 (crontab -e):
    0 6 * * * . /opt/tvde/.env_movvi && /opt/tvde/venv/bin/python3 /opt/tvde/sync_motoristas.py >> /opt/tvde/logs/sync_motoristas.log 2>&1
"""

import os, re, sys, sqlite3, unicodedata
from datetime import datetime
from playwright.sync_api import sync_playwright

# ─────────── CONFIGURAÇÃO ───────────
DB_PATH    = "/opt/tvde/tvde_data.db"
BASE_URL   = "https://movvi.com.pt"
LOGIN_URL  = f"{BASE_URL}/login"            # ajuste se o login for noutro caminho
DRIVERS_URL = f"{BASE_URL}/admin/drivers"
EMAIL      = os.environ.get("MOVVI_EMAIL", "")
PASSWORD   = os.environ.get("MOVVI_PASS", "")
HEADLESS   = True
MIN_ESPERADO = 50    # segurança: se raspar menos que isto, aborta sem tocar na BD

# Registos que não são motoristas reais (não entram no sistema de lavagens)
EXCLUIR = ["manutencao ou sinistro", "manutenção ou sinistro"]
# ─────────────────────────────────────


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def parece_nome(txt):
    """Heurística: célula que parece um nome de pessoa."""
    t = txt.strip()
    if not (5 <= len(t) <= 80):
        return False
    if "@" in t or t.isdigit():
        return False
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{20,}", t.lower()):
        return False
    if not re.fullmatch(r"[A-Za-zÀ-ÿ' .-]+", t):
        return False
    return len(t.split()) >= 2 or len(t) >= 8


def raspar_motoristas():
    """Devolve lista [(nome, ativo_bool)] lida do GesTVDE."""
    resultado = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', locale='pt-PT')
        page = ctx.new_page()

        # ---- login ----
        log(f"A abrir {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        # campos genéricos (Laravel/Bootstrap típico)
        for sel in ('input[type="email"]', 'input[name="email"]',
                    'input[name="username"]', 'input[type="text"]'):
            if page.locator(sel).count():
                page.fill(sel, EMAIL)
                break
        page.fill('input[type="password"]', PASSWORD)
        for sel in ('button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("Entrar")', 'button:has-text("Login")'):
            if page.locator(sel).count():
                page.click(sel)
                break
        page.wait_for_load_state("networkidle", timeout=60000)
        if "login" in page.url.lower():
            browser.close()
            raise RuntimeError("Login falhou — verifique MOVVI_EMAIL/MOVVI_PASS e o LOGIN_URL")
        log("Login OK")

        # ---- página de motoristas ----
        page.goto(DRIVERS_URL, wait_until="networkidle", timeout=60000)

        # tentar aumentar o nº de registos por página (DataTables típico)
        for sel in ('select[name*="length"]', "select.form-select", "select"):
            try:
                if page.locator(sel).first.count():
                    opts = page.locator(sel).first.locator("option").all_inner_texts()
                    alvo = "-1" if any("-1" in o or "Tod" in o for o in opts) else "100"
                    page.locator(sel).first.select_option(alvo)
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        pagina = 1
        while pagina <= 40:
            page.wait_for_timeout(800)
            linhas = page.locator("table tbody tr")
            n = linhas.count()
            log(f"Página {pagina}: {n} linhas")
            for i in range(n):
                cels = linhas.nth(i).locator("td").all_inner_texts()
                estado, nome, mid = None, None, None
                for c in cels:
                    c2 = c.strip()
                    if c2 in ("Ativo", "Activo"):
                        estado = True
                    elif c2 == "Inativo":
                        estado = False
                if estado is None:
                    continue
                for c in cels:
                    if parece_nome(c):
                        nome = c.strip()
                        break
                for c in cels:
                    c2 = c.strip()
                    if c2.isdigit() and len(c2) <= 5 and not c2.startswith("0"):
                        mid = c2
                        break
                if nome:
                    resultado[norm(nome)] = (nome, estado, mid)

            # próxima página
            prox = page.locator('a:has-text("»"), a:has-text("›"), '
                                'li.next a, a[rel="next"], .pagination a:has-text("Seguinte")')
            avancou = False
            for j in range(prox.count()):
                el = prox.nth(j)
                classe = (el.get_attribute("class") or "") + " " + \
                         (el.locator("xpath=..").get_attribute("class") or "")
                if "disabled" not in classe:
                    try:
                        el.click()
                        page.wait_for_load_state("networkidle", timeout=30000)
                        avancou = True
                        break
                    except Exception:
                        pass
            if not avancou:
                break
            pagina += 1

        browser.close()
    return list(resultado.values())


def sincronizar(motoristas):
    excl = set(EXCLUIR)
    motoristas = [m for m in motoristas if norm(m[0]) not in excl]

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    existentes = {norm(r["nome"]): r["nome"]
                  for r in db.execute("SELECT nome FROM motoristas")}
    por_id = {str(r["movvi_driver_id"]): r["nome"]
              for r in db.execute("SELECT movvi_driver_id, nome FROM motoristas")}

    ativados = desativados = novos = 0
    agora = datetime.now().isoformat()

    for nome, ativo, mid in motoristas:
        chave = norm(nome)
        alvo = existentes.get(chave) or (por_id.get(str(mid)) if mid else None)
        if alvo:
            cur = db.execute(
                "UPDATE motoristas SET ativo=?, sincronizado_em=? "
                "WHERE nome=? AND ativo != ?",
                (1 if ativo else 0, agora, alvo, 1 if ativo else 0))
            if cur.rowcount:
                if ativo:
                    ativados += 1
                    log(f"  → reativado: {nome}")
                else:
                    desativados += 1
                    log(f"  → desativado: {nome}")
        else:
            db.execute(
                "INSERT INTO motoristas (movvi_driver_id, nome, ativo, sincronizado_em) VALUES (?,?,?,?)",
                (mid or "gs-" + chave, nome, 1 if ativo else 0, agora))
            novos += 1
            log(f"  → NOVO motorista: {nome} ({'ativo' if ativo else 'inativo'})")

    # quem está na BD mas desapareceu do GesTVDE → desativar (não apagar)
    raspados = {norm(m[0]) for m in motoristas}
    for m in motoristas:
        if m[2] and str(m[2]) in por_id:
            raspados.add(norm(por_id[str(m[2])]))
    for chave, nome_bd in existentes.items():
        if chave not in raspados and chave not in excl:
            cur = db.execute(
                "UPDATE motoristas SET ativo=0, sincronizado_em=? WHERE nome=? AND ativo=1",
                (agora, nome_bd))
            if cur.rowcount:
                desativados += 1
                log(f"  → desativado (removido do GesTVDE): {nome_bd}")

    db.commit()
    total_ativos = db.execute("SELECT COUNT(*) FROM motoristas WHERE ativo=1").fetchone()[0]
    db.close()
    log(f"Resumo: {novos} novos | {ativados} reativados | {desativados} desativados "
        f"| {total_ativos} ativos na BD")




# ─────────── VIATURAS (GesTVDE = fonte unica) ───────────
VEHICLES_URL = f"{BASE_URL}/admin/vehicle-items"
MIN_VIATURAS = 40
PLACA_RE = re.compile(r"^(?:[A-Z]{2}-\d{2}-[A-Z]{2}|\d{2}-[A-Z]{2}-\d{2}|\d{2}-\d{2}-[A-Z]{2}|[A-Z]{2}-\d{2}-\d{2}|[A-Z]-\d{3}-[A-Z]{2})$")


def raspar_viaturas():
    """Devolve lista [(matricula, modelo)] lida do GesTVDE."""
    resultado = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', locale='pt-PT')
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.fill('input[type="email"]', EMAIL)
        page.fill('input[type="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=60000)
        if "login" in page.url.lower():
            browser.close()
            raise RuntimeError("Login falhou (viaturas)")
        page.goto(VEHICLES_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1200)
        linhas = page.locator("table tbody tr")
        n = linhas.count()
        suspensas = 0
        log(f"Viaturas GesTVDE: {n} linhas")
        for i in range(n):
            cels = [c.strip() for c in linhas.nth(i).locator("td").all_inner_texts()]
            mat, marca, modelo, idx_mat = None, "", "", None
            for idx, c in enumerate(cels):
                if PLACA_RE.match(c.upper()):
                    mat = c.upper()
                    idx_mat = idx
                    if idx >= 3:
                        marca, modelo = cels[idx - 3], cels[idx - 2]
                    break
            if not mat:
                continue
            # coluna Suspended (2 a seguir a matricula): <span display:none>0|1</span>
            try:
                susp = linhas.nth(i).locator("td").nth(idx_mat + 2).inner_html()
            except Exception:
                susp = ""
            if ">1<" in susp.replace(" ", "").replace("\n", ""):
                suspensas += 1
                continue        # viatura suspensa no GesTVDE — nao entra
            nome_modelo = " ".join(w for w in (marca.title(), modelo) if w).strip()
            resultado[mat] = nome_modelo
        browser.close()
        log(f"Viaturas suspensas ignoradas: {suspensas}")
    return list(resultado.items())


def sincronizar_viaturas(viaturas):
    if len(viaturas) < MIN_VIATURAS:
        log(f"AVISO: so {len(viaturas)} viaturas raspadas (<{MIN_VIATURAS}) — viaturas NAO alteradas")
        return
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    existentes = {r["matricula"]: (r["modelo"] or "")
                  for r in db.execute("SELECT matricula, modelo FROM viaturas")}
    certas = {m for m, _ in viaturas}
    novas = atualizadas = removidas = 0
    for mat, modelo in viaturas:
        if mat in existentes:
            if modelo and existentes[mat] != modelo:
                db.execute("UPDATE viaturas SET modelo=? WHERE matricula=?", (modelo, mat))
                atualizadas += 1
        else:
            try:
                db.execute("INSERT INTO viaturas (matricula, modelo) VALUES (?,?)", (mat, modelo))
                novas += 1
                log(f"  → NOVA viatura: {mat} ({modelo})")
            except sqlite3.IntegrityError as e:
                log(f"  ⚠ nao inseri {mat}: {e}")
    for mat in existentes:
        if mat not in certas:
            db.execute("DELETE FROM viaturas WHERE matricula=?", (mat,))
            removidas += 1
            log(f"  → removida (nao existe no GesTVDE): {mat}")
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM viaturas").fetchone()[0]
    db.close()
    log(f"Viaturas: {novas} novas | {atualizadas} atualizadas | {removidas} removidas | {total} na BD")


if __name__ == "__main__":
    if not EMAIL or not PASSWORD:
        sys.exit("ERRO: defina MOVVI_EMAIL e MOVVI_PASS (source /opt/tvde/.env_movvi)")
    log("=== Sincronização de motoristas GesTVDE → SQLite ===")
    dados = raspar_motoristas()
    log(f"Raspados {len(dados)} motoristas do GesTVDE "
        f"({sum(1 for d in dados if d[1])} ativos, {sum(1 for d in dados if not d[1])} inativos)")
    if len(dados) < MIN_ESPERADO:
        sys.exit(f"ABORTADO: só encontrei {len(dados)} motoristas (< {MIN_ESPERADO}). "
                 "A página pode ter mudado — a BD não foi alterada.")
    sincronizar(dados)

    # correcoes de viaturas que o pipeline importa errado (origem: Cartrack)
    CORRECOES_VIATURAS = {"202207": "CI-51-OB"}
    db = sqlite3.connect(DB_PATH)
    for errada, certa in CORRECOES_VIATURAS.items():
        existe_certa = db.execute("SELECT COUNT(*) FROM viaturas WHERE matricula=?", (certa,)).fetchone()[0]
        if existe_certa:
            n = db.execute("DELETE FROM viaturas WHERE matricula=?", (errada,)).rowcount
        else:
            n = db.execute("UPDATE viaturas SET matricula=? WHERE matricula=?", (certa, errada)).rowcount
        if n:
            log(f"  → viatura corrigida: {errada} → {certa}")
    db.commit(); db.close()
    viaturas = raspar_viaturas()
    sincronizar_viaturas(viaturas)
    log("Concluído.")
