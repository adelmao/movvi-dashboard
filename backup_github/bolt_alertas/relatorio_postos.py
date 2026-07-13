#!/usr/bin/env python3
"""
relatorio_postos.py — Movvi
PDF formato padrao: Logo MOVVI, colunas #/Posto(Waze)/Morada/Cidade/EUR-kWh
Cron: 30 9 * * 1 cd /root/bolt_alertas && source venv/bin/activate && python3 relatorio_postos.py >> alertas.log 2>&1
"""
import csv, io, requests, urllib.parse, re, logging
from datetime import datetime, timedelta, date
from collections import defaultdict
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [POSTOS] %(message)s")
log = logging.getLogger(__name__)

try:
    from config import META_TOKEN, META_PHONE_ID, META_GESTOR_TEL
except:
    META_TOKEN=""; META_PHONE_ID="1135522376308599"; META_GESTOR_TEL="351913606800"

STORAGE_PATH = "/root/bolt_alertas/prio_session.json"
PRIO_CLIENT  = "6945"
NUM_POSTOS   = 30
DIAS         = 30
PDF_PATH     = "/tmp/movvi_postos.pdf"
MOBIE_CSV    = "https://www.mobie.pt/documents/42032/106470/8602.csv/d8679fe8-51c0-00ce-97b7-37f8a930c861"

def carregar_mobie():
    log.info("A carregar base de dados MOBIE...")
    r = requests.get(MOBIE_CSV, timeout=20)
    r.encoding = "utf-8-sig"
    mobie = {}
    reader = csv.DictReader(io.StringIO(r.text), delimiter=";")
    for row in reader:
        pid = row.get("ID","").strip()
        if pid and pid not in mobie:
            mobie[pid] = {
                "morada":   row.get("MORADA","").strip(),
                "cidade":   row.get("MUNICIPIO","").strip(),
            }
    log.info(f"✅ {len(mobie)} postos MOBIE carregados")
    return mobie

def obter_cookies():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=STORAGE_PATH)
        page = ctx.new_page()
        page.goto("https://www.myprio.com/Transactions/Transactions?TradIsElectric=True", timeout=30000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        browser.close()
    nr2  = urllib.parse.unquote(cookies.get("nr2Users",""))
    csrf = re.search(r'crf=([^;]+)', nr2)
    return cookies, (csrf.group(1) if csrf else "")

def obter_transaccoes():
    data_fim    = datetime.now().strftime("%Y-%m-%d")
    data_inicio = (datetime.now() - timedelta(days=DIAS)).strftime("%Y-%m-%d")
    log.info(f"A obter transaccoes {data_inicio} -> {data_fim}...")
    cookies, csrf = obter_cookies()
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json","Content-Type":"application/json; charset=UTF-8","Origin":"https://www.myprio.com","Referer":"https://www.myprio.com/Transactions/Transactions?TradIsElectric=True","OutSystems-locale":"pt-PT","X-CSRFToken":csrf})
    for k,v in cookies.items(): s.cookies.set(k,v)
    payload = {"versionInfo":{"moduleVersion":"q7ttCInsveu0XCNFHjl9hg","apiVersion":"1RCPxxNAORpYWU1ybvzk6A"},"viewName":"TransactionsFlow.Transactions","screenData":{"variables":{"TradIsElectric":True,"ClientId":PRIO_CLIENT,"IsFleet":True,"MaxRecords":500,"StartIndex":0,"isRefresh":True,"FiltersElectricTransactions_OAPI":{"StartDate":data_inicio,"EndDate":data_fim,"CardStatusId":"0","StationId":"0","StationCountryId":"0","CardNumbers":"","InvoicesList":"","IsProfessional":False,"StationTypeId":"0","isPrioGo":False,"ChargeStationId":"","UserCardGroup":"0","DriversNames":"","CardFormatID":"0","IsWaitingInvoice":False,"CarPlates":""}}}}
    r = s.post("https://www.myprio.com/Transactions/screenservices/Transactions/TransactionsFlow/Transactions/DataActionTransactionsMobieList", json=payload, timeout=30)
    trans = r.json().get("data",{}).get("MobieTransactions",{}).get("ElectricTransactionsList",{}).get("List",[])
    log.info(f"✅ {len(trans)} transaccoes")
    return trans

def processar_postos(transaccoes, mobie):
    postos = defaultdict(lambda: {"usos":0,"kwh_total":0.0,"custo_total":0.0})
    for t in transaccoes:
        pid   = (t.get("ChargeStation") or t.get("IdChargingStation") or "?").strip()
        kwh   = float(t.get("Energy") or 0)
        custo = float(t.get("TotalValueWithTaxes") or t.get("TotalValue") or 0)
        postos[pid]["usos"]        += 1
        postos[pid]["kwh_total"]   += kwh
        postos[pid]["custo_total"] += custo

    result = []
    for pid, d in postos.items():
        if d["kwh_total"] > 0:
            info = mobie.get(pid, {"morada":"","cidade":""})
            result.append({
                "id":      pid,
                "morada":  info["morada"],
                "cidade":  info["cidade"],
                "usos":    d["usos"],
                "kwh":     d["kwh_total"],
                "custo":   d["custo_total"],
                "preco":   round(d["custo_total"]/d["kwh_total"], 3),
            })
    result.sort(key=lambda x: x["preco"])
    return result[:NUM_POSTOS]

def gerar_pdf(postos, semana, ano, data_str):
    from fpdf import FPDF
    
    class PDF(FPDF):
        def header(self):
            # Logo MOVVI centrado
            self.set_font("Helvetica","B",28)
            self.set_text_color(0,0,0)
            self.image("/root/bolt_alertas/logomovvi.jpg", x=70, y=8, w=70)
            self.ln(22)
            
            self.set_text_color(100,100,100)
            
            # Linha separadora
            self.set_draw_color(180,180,180)
            self.line(10, self.get_y()+2, 200, self.get_y()+2)
            self.ln(6)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica","",7)
            self.set_text_color(100,100,100)
            self.cell(0,5,"(c) MOVVI TVDE  |  Uso interno e confidencial  |  Tel: 913 606 854",align="C")

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(12,10,12)

    # Titulo
    pdf.set_font("Helvetica","B",10)
    pdf.set_text_color(0,0,0)
    pdf.cell(0,6,"Top 30 ordenado por EUR/kWh (c/ IVA)  |  Clique no posto para abrir no Waze",align="C",ln=True)
    pdf.set_font("Helvetica","",8)
    pdf.set_text_color(80,80,80)
    pdf.cell(0,5,f"Relatorio Semanal  -  Semana {semana}/{ano}  -  Data: {data_str}",align="C",ln=True)
    pdf.cell(0,5,f"Relatorio nr {semana}/{ano}",align="C",ln=True)
    pdf.ln(2)

    # Cabecalho tabela
    pdf.set_fill_color(240,240,240)
    pdf.set_text_color(0,0,0)
    pdf.set_font("Helvetica","B",8)
    pdf.set_draw_color(200,200,200)
    cols = [8, 28, 100, 38, 16]
    hdrs = ["#", "Posto (Waze)", "Morada", "Cidade", "EUR/kWh"]
    for w,h in zip(cols,hdrs):
        pdf.cell(w,7,h,border=1,fill=True,align="C")
    pdf.ln()

    # Linhas
    pdf.set_font("Helvetica","",8)
    for i, p in enumerate(postos):
        fill = (i%2==0)
        pdf.set_fill_color(250,250,250) if fill else pdf.set_fill_color(255,255,255)

        # Numero
        pdf.set_text_color(0,0,0)
        pdf.cell(cols[0],6,str(i+1),border="LRB",fill=fill,align="C")

        # Posto com link Waze
        waze = f"https://waze.com/ul?q={urllib.parse.quote(p['id'])}" 
        pdf.set_text_color(0,0,200)
        pdf.cell(cols[1],6,p["id"],border="LRB",fill=fill,align="C",link=waze)
        pdf.set_text_color(0,0,0)

        # Morada
        morada = p["morada"][:52] if p["morada"] else ""
        pdf.cell(cols[2],6,morada,border="LRB",fill=fill)

        # Cidade
        cidade = p["cidade"][:20] if p["cidade"] else ""
        pdf.cell(cols[3],6,cidade,border="LRB",fill=fill)

        # EUR/kWh em bold
        pdf.set_font("Helvetica","B",8)
        pdf.cell(cols[4],6,f"{p['preco']:.3f}",border="LRB",fill=fill,align="C")
        pdf.set_font("Helvetica","",8)
        pdf.ln()

    pdf.output(PDF_PATH)
    log.info(f"✅ PDF: {PDF_PATH}")

def enviar_pdf(semana, ano):
    auth = {"Authorization":f"Bearer {META_TOKEN}"}
    with open(PDF_PATH,"rb") as f:
        up = requests.post(f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/media",headers=auth,files={"file":(f"Movvi_Postos_Semana{semana}_{ano}.pdf",f,"application/pdf")},data={"messaging_product":"whatsapp"},timeout=30)
    up.raise_for_status()
    mid = up.json()["id"]
    msg = requests.post(f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages",headers={**auth,"Content-Type":"application/json"},json={"messaging_product":"whatsapp","to":META_GESTOR_TEL,"type":"document","document":{"id":mid,"filename":f"Postos_Baratos_Movvi_Semana{semana}_{ano}.pdf","caption":f"*MOVVI - Postos de Carregamento*\nSemana {semana}/{ano}\nTop 30 postos mais baratos da frota"}},timeout=15)
    msg.raise_for_status()
    log.info("✅ PDF enviado!")

MAX_TENTATIVAS = 6
INTERVALO_RETRY = 300

def main_once():
    hoje    = date.today()
    semana  = hoje.isocalendar()[1]
    ano     = hoje.year
    data_str= hoje.strftime("%d/%m/%Y")

    log.info(f"=== Relatorio Postos Movvi - Semana {semana}/{ano} ===")
    mobie       = carregar_mobie()
    transaccoes = obter_transaccoes()
    if not transaccoes:
        from whatsapp_meta import enviar_whatsapp
        enviar_whatsapp(f"Movvi Postos - Sem dados MyPRIO. Semana {semana}/{ano}")
        return
    postos = processar_postos(transaccoes, mobie)
    log.info(f"✅ {len(postos)} postos processados")
    gerar_pdf(postos, semana, ano, data_str)
    enviar_pdf(semana, ano)
    log.info("=== Concluido ===")

def main():
    import time as _time
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        log.info(f"Tentativa {tentativa}/{MAX_TENTATIVAS}...")
        try:
            main_once()
            return
        except Exception as e:
            log.error(f"Tentativa {tentativa} falhou: {e}")
            if tentativa < MAX_TENTATIVAS:
                log.info(f"A tentar novamente em {INTERVALO_RETRY//60} minutos...")
                from whatsapp_meta import enviar_whatsapp
                if tentativa == 1:
                    enviar_whatsapp(
                        "Movvi Postos - Sessao PRIO expirada.\n"
                        "Renova com renovar_prio.bat no Desktop.\n"
                        f"Vou tentar mais {MAX_TENTATIVAS - tentativa} vezes a cada 5 min."
                    )
                _time.sleep(INTERVALO_RETRY)
            else:
                log.error("Todas as tentativas falharam.")
                from whatsapp_meta import enviar_whatsapp
                enviar_whatsapp(
                    "Movvi Postos - FALHOU apos 6 tentativas.\n"
                    "Renova a sessao PRIO e corre manualmente:\n"
                    "python3 /root/bolt_alertas/relatorio_postos.py"
                )

if __name__=="__main__":
    main()

def verificar_sessao_prio():
    """Testa se a sessao PRIO ainda e valida. Se nao, avisa por WhatsApp."""
    try:
        cookies, csrf = obter_cookies()
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json","Content-Type":"application/json; charset=UTF-8","Origin":"https://www.myprio.com","X-CSRFToken":csrf})
        for k,v in cookies.items(): s.cookies.set(k,v)
        payload = {"versionInfo":{"moduleVersion":"q7ttCInsveu0XCNFHjl9hg","apiVersion":"1RCPxxNAORpYWU1ybvzk6A"},"viewName":"TransactionsFlow.Transactions","screenData":{"variables":{"TradIsElectric":True,"ClientId":PRIO_CLIENT,"IsFleet":True,"MaxRecords":1,"StartIndex":0,"isRefresh":True,"FiltersElectricTransactions_OAPI":{"StartDate":"2026-01-01","EndDate":"2026-01-02","CardStatusId":"0","StationId":"0","StationCountryId":"0","CardNumbers":"","InvoicesList":"","IsProfessional":False,"StationTypeId":"0","isPrioGo":False,"ChargeStationId":"","UserCardGroup":"0","DriversNames":"","CardFormatID":"0","IsWaitingInvoice":False,"CarPlates":""}}}}
        r = s.post("https://www.myprio.com/Transactions/screenservices/Transactions/TransactionsFlow/Transactions/DataActionTransactionsMobieList", json=payload, timeout=15)
        data = r.json()
        if data.get("exception"):
            raise Exception("Sessao expirada")
        log.info("Sessao PRIO valida.")
        return True
    except Exception as e:
        log.warning(f"Sessao PRIO invalida: {e}")
        from whatsapp_meta import enviar_whatsapp
        enviar_whatsapp(
            "⚠️ *MOVVI - Sessao MyPRIO Expirada*\n"
            "A sessao do portal PRIO expirou.\n\n"
            "Para renovar:\n"
            "1. Abre o CMD no Windows\n"
            "2. Corre: py Downloads\\prio_login.py\n"
            "3. Faz login com o codigo SMS\n"
            "4. Corre: scp %USERPROFILE%\\Downloads\\prio_session.json root@178.104.20.109:/root/bolt_alertas/\n\n"
            "_Movvi Fleet - Aviso automatico_"
        )
        return False
