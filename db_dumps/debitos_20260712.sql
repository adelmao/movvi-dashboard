PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE debitos_carregamento(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER, driver_nome TEXT, license_plate TEXT,
        charger_id INTEGER, charger_nome TEXT,
        kwh REAL, preco_kwh REAL, valor REAL,
        inicio TEXT, fim TEXT, semana TEXT,
        auto INTEGER DEFAULT 0,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
COMMIT;
