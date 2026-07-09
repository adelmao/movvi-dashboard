#!/bin/bash
# Watchdog do mapa da frota — corre via cron a cada 5 min

DB="/opt/tvde/tvde_data.db"
LOG="/opt/tvde/logs/watchdog_mapa.log"
TS=$(date '+%Y-%m-%d %H:%M:%S')

# 1. garantir tabela existe
sqlite3 "$DB" "CREATE TABLE IF NOT EXISTS frota_mapa (
    matricula TEXT PRIMARY KEY, motorista TEXT,
    lat REAL, lng REAL, velocidade INTEGER, ignicao INTEGER,
    app TEXT, movendo INTEGER, parado_desde TEXT, atualizado_em TEXT);" 2>/dev/null

# 2. verificar daemon mapa-frota
if ! systemctl is-active --quiet mapa-frota; then
    echo "$TS mapa-frota parado — a reiniciar" >> "$LOG"
    systemctl start mapa-frota
fi

# 3. verificar servidor standalone
if ! systemctl is-active --quiet mapa-servidor; then
    echo "$TS mapa-servidor parado — a reiniciar" >> "$LOG"
    systemctl start mapa-servidor
fi

# 4. verificar dashboard
if ! systemctl is-active --quiet tvde-servidor; then
    echo "$TS tvde parado — a reiniciar" >> "$LOG"
    fuser -k 5000/tcp 2>/dev/null
    sleep 2
    systemctl start tvde-servidor
fi

# 5. verificar se tabela tem dados recentes
ULTIMA=$(sqlite3 "$DB" "SELECT MAX(atualizado_em) FROM frota_mapa;" 2>/dev/null)
if [ -z "$ULTIMA" ]; then
    echo "$TS tabela vazia — a reiniciar mapa-frota" >> "$LOG"
    systemctl restart mapa-frota
fi

# 6. verificar se API responde em menos de 5s
HTTP=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://localhost:5001/api/frota/mapa 2>/dev/null)
if [ "$HTTP" != "200" ]; then
    echo "$TS mapa-servidor nao responde (HTTP $HTTP) — a reiniciar" >> "$LOG"
    systemctl restart mapa-servidor
fi
