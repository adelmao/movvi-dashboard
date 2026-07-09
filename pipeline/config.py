# =============================================================================
# TVDE FLEET PIPELINE - CONFIGURAÇÃO
# =============================================================================

# --- BOLT FLEET API ---
BOLT_CLIENT_ID     = "f39gyf7NMmNDQhUq-Aj5j"
BOLT_CLIENT_SECRET = "ujEzh0nC8cX_rMUX9I1s27McTeW4unpbRtPgPl_nzbihTVcGyNwUQUNHbU8ZF2oRYAReTJCD3ncus_42PsBu4g"
BOLT_TOKEN_URL     = "https://oidc.bolt.eu/token"
BOLT_API_BASE      = "https://api.fleets.bolt.eu/v1"

# --- BOLT JWT TOKEN (portal web) ---
# Como renovar: fleets.bolt.eu -> F12 -> Network -> qualquer pedido -> Cabeçalhos -> authorization
# Copie o valor SEM "Bearer "
BOLT_JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkYXRhIjp7InR5cGUiOiJjb21wYW55IiwiZmxlZXRfb3duZXJfaWQiOjE3OTU3LCJjb21wYW55Ijp7ImNvbXBhbnlfdHlwZSI6ImZsZWV0X2NvbXBhbnkiLCJjb21wYW55X2lkIjoxOTI1MiwicGVybWlzc2lvbnMiOlsiZmluYW5jaWFsczp2aWV3IiwiZHJpdmVyczp2aWV3IiwidmVoaWNsZXM6dmlldyIsImRyaXZlcl9hcHBsaWNhdGlvbnM6dmlldyIsInZlaGljbGVfYXBwbGljYXRpb25zOnZpZXciLCJjb21wYW55X2FjY2Vzczp2aWV3IiwiZmluYW5jaWFsczp3cml0ZSIsImRyaXZlcnM6d3JpdGUiLCJ2ZWhpY2xlczp3cml0ZSIsImRyaXZlcl9hcHBsaWNhdGlvbnM6d3JpdGUiLCJ2ZWhpY2xlX2FwcGxpY2F0aW9uczp3cml0ZSIsImNvbXBhbnlfYWNjZXNzOndyaXRlIl19fSwiaWF0IjoxNzgwNzY3NTc2LCJleHAiOjE3ODA3Njg0NzZ9._9JXAJamwfIdZ-cQqeK_CN3YrAeT3gHzGXYLaxUeOzw"

# --- CARTRACK API ---
CARTRACK_USERNAME  = "ADEL00005"
CARTRACK_PASSWORD  = "c75de3969410f4c2cda1ba9bdaa5eab50f8c8b93ba7f49069c3f03245ff416ae"
CARTRACK_BASE_URL  = "https://fleetapi-pt.cartrack.com"

# --- MOVVI / GESTVDE API ---
MOVVI_API_BASE = "https://movvi.com.pt"
MOVVI_EMAIL    = "adelmao@gmail.com"
MOVVI_PASSWORD = "Kavila02@"

# --- UBER (CSV manual) ---
UBER_CSV_FOLDER = r"C:\TVDE\uber_exports"

# --- BASE DE DADOS LOCAL ---
DB_PATH = r"C:\TVDE\tvde_data.db"

# --- RELATÓRIOS ---
REPORTS_FOLDER = r"C:\TVDE\relatorios"

# --- AGENDAMENTO ---
SCHEDULE_TIME = "08:30"

BOLT_EMAIL    = "adelmotop10@gmail.com"
BOLT_PASSWORD = "Pedrouber90"

UBER_EMAIL    = "adelmotop10@gmail.com"
UBER_PASSWORD = "Pedrouber90"

UBER_CSV_FOLDER = r"C:\TVDE\uber_exports"

BOLT_REFRESH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkYXRhIjp7InR5cGUiOiJiYXNlIiwiZmxlZXRfb3duZXJfaWQiOjE3OTU3LCJqdGkiOiIxYTQwNDMyMC1mYjUyLTQyZjUtOTM0YS0yMWNhYWQzYzkyYzUifSwiaWF0IjoxNzc4MDA5OTk0LCJleHAiOjE3Nzg2MTQ3OTR9.lZTdnbAzYMeJWVtE-v-Zs419xJiKknNRqxhKPCpLUqU"

PRIO_USERNAME = "122469"
PRIO_PASSWORD = "Pedrouber90@@@"
# Cookies Uber (actualizar quando expirarem)

UBER_SID  = "QA.CAESEBl3l-nL90YurPE_PJN-TDkY2-nR1wYiATEqJDVmNzJhNWYyLTc0ZTItNGI0ZS05NjY3LTE2OGMwYmI1OTg0MTJA8FqixAw6HB3hwyy4SN6NETe3IMTIAyiyVGIfFo8jSG4I-nCsi0Kqjx_Dr63ag6kHQ--e39rfxXVJO7kcAJzqPjoBMUIIdWJlci5jb20.2iPRrZdSp9-uqbgEDDDW27f7F6rHrQXEcFbz5fhBMtk"
UBER_JWT  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkYXRhIjp7InN1cHBsaWVyT3JnVVVJRCI6IjVmNzJhNWYyLTc0ZTItNGI0ZS05NjY3LTE2OGMwYmI1OTg0MSIsInN1cHBsaWVyT3JnVHlwZXMiOiJEUklWRVJfQlVTSU5FU1N8U1VQUExJRVJfRkxFRVR8TVVMVElfRFJJVkVSX0JVU0lORVNTIiwidGVuYW5jeSI6InViZXIvcHJvZHVjdGlvbiJ9LCJpYXQiOjE3Nzg4NDcyNDcsImV4cCI6MTc3ODkzMzY0N30.fuDK8pXV81JRpiecmIRbIReUpzclbfgOKCeijmLSTxM"
UBER_CSID = "1.1781445595407.rsIn/vX0oFFxAypNusrddslrypxlWTfvpoF+KZSY+Vg="
