#!/usr/bin/env python3
import os, time, subprocess
ALVO = "/opt/tvde/tvde_data.db"
LOG = "/opt/tvde/logs/vigia_bd.log"
def stamp(): return time.strftime("%Y-%m-%d %H:%M:%S")
ultimo = os.stat(ALVO).st_ino
with open(LOG, "a") as f:
    f.write(f"[{stamp()}] vigia iniciado, inode={ultimo}\n")
while True:
    time.sleep(3)
    try:
        atual = os.stat(ALVO).st_ino
    except FileNotFoundError:
        atual = None
    if atual != ultimo:
        with open(LOG, "a") as f:
            f.write(f"\n[{stamp()}] *** FICHEIRO TROCADO *** inode {ultimo} -> {atual}\n")
            f.write(subprocess.run(["ps","auxww"], capture_output=True, text=True).stdout)
            f.write("\n" + "="*60 + "\n")
        ultimo = atual
