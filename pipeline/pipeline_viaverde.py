"""
TVDE Fleet — Pipeline Via Verde v2
Adelmo Top Unipessoal Lda

Guarda transacções por dia+matrícula para ligar ao motorista.
Corre automaticamente aos domingos às 17:00 via Task Scheduler.

Localização: C:\TVDE\pipeline\pipeline_viaverde.py
"""

import os, sqlite3, glob, logging
from datetime import datetime
import pandas as pd

# ── Configuração ───────────────────────────────────────────
DB_PATH   = "/opt/tvde/tvde_data.db"
VV_FOLDER = "/opt/tvde/pipeline/viaverde"   # pasta onde colocas os Excel da Via Verde (via Dropbox sync ou upload manual)
LOG_FILE  = "/opt/tvde/pipeline/logs/viaverde.log"

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def criar_tabelas(conn):
    # Tabela de resumo semanal por matrícula (mantida para compatibilidade)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS via_verde (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            semana         TEXT NOT NULL,
            data_inicio    TEXT NOT NULL,
            data_fim       TEXT NOT NULL,
            matricula      TEXT NOT NULL,
            num_transacoes INTEGER DEFAULT 0,
            total_euros    REAL DEFAULT 0,
            ficheiro       TEXT,
            importado_em   TEXT NOT NULL,
            UNIQUE(semana, matricula)
        )
    """)
    # Tabela diária — permite ligar ao motorista via atribuicoes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS via_verde_diario (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            data           TEXT NOT NULL,
            matricula      TEXT NOT NULL,
            num_transacoes INTEGER DEFAULT 0,
            total_euros    REAL DEFAULT 0,
            semana         TEXT NOT NULL,
            importado_em   TEXT NOT NULL,
            UNIQUE(data, matricula)
        )
    """)
    conn.commit()


def encontrar_ficheiro_mais_recente():
    padrao = os.path.join(VV_FOLDER, "**", "*.xlsx")
    ficheiros = glob.glob(padrao, recursive=True)
    if not ficheiros:
        ficheiros = glob.glob(os.path.join(VV_FOLDER, "*.xlsx"))
    return max(ficheiros, key=os.path.getmtime) if ficheiros else None


def processar_ficheiro(ficheiro):
    df = pd.read_excel(ficheiro)
    df.columns = df.columns.str.strip()
    for col in ['Matrícula', 'Valor Transação', 'Data Saída']:
        if col not in df.columns:
            raise ValueError(f"Coluna '{col}' não encontrada. Colunas: {df.columns.tolist()}")
    df['Valor Transação'] = pd.to_numeric(df['Valor Transação'], errors='coerce').fillna(0)
    df['Data Saída'] = pd.to_datetime(df['Data Saída'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['Matrícula', 'Data Saída'])
    df = df[df['Matrícula'].str.strip() != '']
    df['data'] = df['Data Saída'].dt.strftime('%Y-%m-%d')
    df['semana'] = df['Data Saída'].dt.strftime('%Y-W%W')
    return df


def importar(df, nome_ficheiro, conn):
    importado_em = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ins_sem = upd_sem = ins_dia = upd_dia = 0

    # ── Resumo semanal ──────────────────────────────────────
    sem_group = df.groupby(['semana', 'Matrícula']).agg(
        num_transacoes=('Valor Transação', 'count'),
        total_euros=('Valor Transação', 'sum'),
        data_inicio=('data', 'min'),
        data_fim=('data', 'max')
    ).reset_index()

    for _, r in sem_group.iterrows():
        exists = conn.execute(
            "SELECT id FROM via_verde WHERE semana=? AND matricula=?",
            (r['semana'], r['Matrícula'])
        ).fetchone()
        if exists:
            conn.execute("""
                UPDATE via_verde SET num_transacoes=?, total_euros=?,
                data_inicio=?, data_fim=?, ficheiro=?, importado_em=?
                WHERE semana=? AND matricula=?
            """, (int(r['num_transacoes']), round(float(r['total_euros']),2),
                  r['data_inicio'], r['data_fim'], nome_ficheiro, importado_em,
                  r['semana'], r['Matrícula']))
            upd_sem += 1
        else:
            conn.execute("""
                INSERT INTO via_verde
                (semana, data_inicio, data_fim, matricula, num_transacoes,
                 total_euros, ficheiro, importado_em)
                VALUES (?,?,?,?,?,?,?,?)
            """, (r['semana'], r['data_inicio'], r['data_fim'], r['Matrícula'],
                  int(r['num_transacoes']), round(float(r['total_euros']),2),
                  nome_ficheiro, importado_em))
            ins_sem += 1

    # ── Diário por matrícula ────────────────────────────────
    dia_group = df.groupby(['data', 'Matrícula', 'semana']).agg(
        num_transacoes=('Valor Transação', 'count'),
        total_euros=('Valor Transação', 'sum')
    ).reset_index()

    for _, r in dia_group.iterrows():
        exists = conn.execute(
            "SELECT id FROM via_verde_diario WHERE data=? AND matricula=?",
            (r['data'], r['Matrícula'])
        ).fetchone()
        if exists:
            conn.execute("""
                UPDATE via_verde_diario
                SET num_transacoes=?, total_euros=?, semana=?, importado_em=?
                WHERE data=? AND matricula=?
            """, (int(r['num_transacoes']), round(float(r['total_euros']),2),
                  r['semana'], importado_em, r['data'], r['Matrícula']))
            upd_dia += 1
        else:
            conn.execute("""
                INSERT INTO via_verde_diario
                (data, matricula, num_transacoes, total_euros, semana, importado_em)
                VALUES (?,?,?,?,?,?)
            """, (r['data'], r['Matrícula'],
                  int(r['num_transacoes']), round(float(r['total_euros']),2),
                  r['semana'], importado_em))
            ins_dia += 1

    conn.commit()
    return ins_sem, upd_sem, ins_dia, upd_dia


def main():
    log.info("=" * 50)
    log.info("Pipeline Via Verde v2 iniciado")

    ficheiro = encontrar_ficheiro_mais_recente()
    if not ficheiro:
        log.error(f"Nenhum ficheiro .xlsx encontrado em: {VV_FOLDER}")
        print(f"ERRO: Nenhum ficheiro encontrado em:\n   {VV_FOLDER}")
        return

    print(f"Ficheiro: {os.path.basename(ficheiro)}")
    log.info(f"Ficheiro: {ficheiro}")

    try:
        df = processar_ficheiro(ficheiro)
    except Exception as e:
        log.error(f"Erro ao processar: {e}")
        print(f"ERRO: {e}")
        return

    total = df['Valor Transação'].sum().round(2)
    print(f"Periodo: {df['data'].min()} -> {df['data'].max()}")
    print(f"Total: {total}EUR | {len(df)} transaccoes | {df['Matricula' if 'Matricula' in df.columns else 'Matrícula'].nunique()} viaturas")

    conn = get_db()
    try:
        criar_tabelas(conn)
        ins_sem, upd_sem, ins_dia, upd_dia = importar(df, os.path.basename(ficheiro), conn)
        log.info(f"Semanal: {ins_sem} novos, {upd_sem} actualizados")
        log.info(f"Diario: {ins_dia} novos, {upd_dia} actualizados")
        print(f"BD actualizada: semanal {ins_sem}+{upd_sem} | diario {ins_dia}+{upd_dia}")
        print("Pipeline concluido!")
    except Exception as e:
        log.error(f"Erro BD: {e}")
        print(f"ERRO BD: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
