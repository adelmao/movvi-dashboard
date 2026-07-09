#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Instalador do módulo de Lavagens no servidor.py do dashboard.movvi.com.pt
==========================================================================
O que faz:
  1. Cria backup: servidor.py.bak-AAAAMMDD-HHMMSS
  2. Adiciona `import lavagens_mod` junto aos imports
  3. Insere a delegação no início de do_GET e do_POST
  4. Mostra o que mudou

Uso no VPS:
  cd /opt/tvde/dashboard
  python3 instalar_lavagens.py

Depois reiniciar o servidor:
  pkill -9 -f servidor.py; sleep 1
  nohup /opt/tvde/venv/bin/python servidor.py > logs/servidor.log 2>&1 &
"""

import re, shutil, sys, os
from datetime import datetime

SERVIDOR = "servidor.py"

if not os.path.exists(SERVIDOR):
    sys.exit(f"ERRO: {SERVIDOR} não encontrado. Corra este script dentro de /opt/tvde/dashboard")
if not os.path.exists("lavagens_mod.py"):
    sys.exit("ERRO: lavagens_mod.py não encontrado nesta pasta. Copie-o primeiro.")

src = open(SERVIDOR, encoding="utf-8").read()

if "lavagens_mod" in src:
    sys.exit("Já instalado — o servidor.py já referencia lavagens_mod. Nada a fazer.")

backup = f"{SERVIDOR}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
shutil.copy(SERVIDOR, backup)
print(f"✓ Backup criado: {backup}")

# 1) import junto aos imports existentes (após o último import do topo)
linhas = src.split("\n")
ultimo_import = 0
for i, l in enumerate(linhas[:80]):
    if re.match(r"^(import |from )\w", l):
        ultimo_import = i
linhas.insert(ultimo_import + 1, "import lavagens_mod  # módulo de lavagens Movvi")
src = "\n".join(linhas)
print(f"✓ Import adicionado após a linha {ultimo_import + 1}")

# 2) delegação em do_GET e do_POST (primeira ocorrência de cada)
def inserir_delegacao(codigo, metodo, verbo):
    padrao = re.compile(rf"(def {metodo}\(self\):\n)([ \t]+)")
    m = padrao.search(codigo)
    if not m:
        print(f"⚠ AVISO: não encontrei 'def {metodo}(self):' — adicione manualmente:")
        print(f"    if lavagens_mod.handle(self, \"{verbo}\"): return")
        return codigo
    indent = m.group(2)
    novo = m.group(1) + indent + f'if lavagens_mod.handle(self, "{verbo}"): return\n' + indent
    codigo = padrao.sub(novo, codigo, count=1)
    print(f"✓ Delegação inserida em {metodo}")
    return codigo

src = inserir_delegacao(src, "do_GET", "GET")
src = inserir_delegacao(src, "do_POST", "POST")

# validar sintaxe antes de gravar
try:
    compile(src, SERVIDOR, "exec")
except SyntaxError as e:
    sys.exit(f"ERRO de sintaxe após alteração (linha {e.lineno}): {e.msg}\n"
             f"Nada foi gravado. O original está intacto e o backup em {backup}.")

open(SERVIDOR, "w", encoding="utf-8").write(src)
print(f"""
✓ Instalado com sucesso!

Agora reinicie o servidor:
  pkill -9 -f servidor.py; sleep 1
  nohup /opt/tvde/venv/bin/python servidor.py > logs/servidor.log 2>&1 &

E aceda a:
  https://dashboard.movvi.com.pt/lavagens          (motoristas)
  https://dashboard.movvi.com.pt/lavagens/admin?chave=movvi2026   (gestão)

Se algo correr mal, reverta com:
  cp {backup} servidor.py
""")
