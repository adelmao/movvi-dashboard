#!/usr/bin/env python3
import json, sqlite3
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 5001
DB   = '/opt/tvde/tvde_data.db'
HTML = '/opt/tvde/static/mapa.html'

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == '/mapa':
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(open(HTML,'rb').read())
            return
        if self.path == '/api/frota/mapa':
            con = sqlite3.connect(DB, timeout=30)
            con.row_factory = sqlite3.Row
            try: rows = con.execute('SELECT * FROM frota_mapa').fetchall()
            except: rows = []
            agora = datetime.now()
            carros = []
            for r in rows:
                pm = 0
                if r['parado_desde']:
                    try: pm = int((agora-datetime.fromisoformat(r['parado_desde'])).total_seconds()//60)
                    except: pass
                fat = 0; km = 0
                try:
                    # se houver varios motoristas hoje, usar o que tem mais faturacao
                    mid_row = con.execute(
                        "SELECT a.motorista_id FROM atribuicoes a "
                        "JOIN viaturas vt ON vt.id=a.viatura_id "
                        "WHERE vt.matricula=? AND a.data=date('now') "
                        "ORDER BY a.id DESC LIMIT 1",
                        (r['matricula'],)).fetchone()
                    mid = mid_row[0] if mid_row else None
                    if mid:
                        bolt = con.execute(
                            "SELECT COALESCE(SUM(faturacao_liquida),0) FROM faturacao_bolt "
                            "WHERE motorista_id=? AND data>=date('now','weekday 1','-7 days')",
                            (mid,)).fetchone()[0] or 0
                        uber = con.execute(
                            "SELECT COALESCE(SUM(faturacao_bruta),0) FROM faturacao_uber_live "
                            "WHERE motorista_id=? AND data>=date('now','weekday 1','-7 days')",
                            (mid,)).fetchone()[0] or 0
                        km_r = con.execute(
                            "SELECT COALESCE(SUM(km_total),0) FROM km_viaturas "
                            "WHERE matricula=? AND data>=date('now','weekday 1','-7 days')",
                            (r['matricula'],)).fetchone()[0] or 0
                        fat=round(bolt+uber,2); km=round(km_r,1)
                except: pass
                carros.append({
                    'id':r['matricula'],'motorista':r['motorista'] or '-',
                    'lat':r['lat'],'lng':r['lng'],'vel':r['velocidade'],
                    'app':r['app'],'movendo':bool(r['movendo']),'paradoMin':pm,
                    'alerta':(not r['movendo'] and pm>=30),
                    'atualizado':r['atualizado_em'],
                    'fat_semana':fat,'km_semana':km,
                })
            con.close()
            body = json.dumps({'carros':carros,'ts':agora.isoformat(timespec='seconds')}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

if __name__ == '__main__':
    print(f'Mapa standalone porta {PORT}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
