#!/usr/bin/env python3
import json, sqlite3, sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 5001
DB   = '/opt/tvde/tvde_data.db'
HTML = '/opt/tvde/static/mapa.html'

# sessoes partilhadas com o dashboard via ficheiro
SESSIONS_FILE = '/tmp/tvde_sessions.json'

def get_session(cookie_header):
    """Le sessoes activas gravadas pelo dashboard."""
    if not cookie_header:
        return None
    token = None
    for part in cookie_header.split(';'):
        part = part.strip()
        if part.startswith('tvde_session='):
            token = part[len('tvde_session='):]
            break
    if not token:
        return None
    try:
        sessions = json.load(open(SESSIONS_FILE))
        return sessions.get(token)
    except:
        return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        sess = get_session(self.headers.get('Cookie',''))

        if self.path == '/mapa':
            if not sess:
                self.send_response(302)
                self.send_header('Location','https://dashboard.movvi.com.pt/login')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(open(HTML,'rb').read())

        elif self.path == '/api/frota/mapa':
            if not sess:
                self.send_response(401)
                self.end_headers()
                return
            con = sqlite3.connect(DB)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute('SELECT * FROM frota_mapa').fetchall()
            except:
                rows = []
            con.close()
            agora = datetime.now()
            carros = []
            for r in rows:
                parado_min = 0
                if r['parado_desde']:
                    try:
                        parado_min = int((agora - datetime.fromisoformat(
                            r['parado_desde'])).total_seconds()//60)
                    except:
                        pass
                carros.append({
                    'id': r['matricula'], 'motorista': r['motorista'] or '-',
                    'lat': r['lat'], 'lng': r['lng'], 'vel': r['velocidade'],
                    'app': r['app'], 'movendo': bool(r['movendo']),
                    'paradoMin': parado_min,
                    'alerta': (r['app']=='offline' and not r['movendo'] and parado_min>=30),
                    'atualizado': r['atualizado_em'],
                })
            body = json.dumps({'carros': carros,
                               'ts': agora.isoformat(timespec='seconds')}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

if __name__ == '__main__':
    print(f'Mapa frota porta {PORT}')
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
