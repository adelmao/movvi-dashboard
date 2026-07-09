from pathlib import Path
import os

BASE_DIR = Path("/opt/tvde")

# Base de dados
DB_PATH = BASE_DIR / "tvde_data.db"

# Diretórios
DASHBOARD_DIR = BASE_DIR / "dashboard"
STATIC_DIR = BASE_DIR / "static"
LOG_DIR = BASE_DIR / "logs"
BACKUP_DIR = BASE_DIR / "backups"

# Aplicação
HOST = "127.0.0.1"
PORT = 5000

# Timezone
TZ = "Europe/Lisbon"

# Ambiente
ENV = os.getenv("MOVVI_ENV", "production")
