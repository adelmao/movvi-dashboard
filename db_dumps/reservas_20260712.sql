PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE reservas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER, driver_nome TEXT, license_plate TEXT,
        charger_id INTEGER, charger_nome TEXT,
        inicio TEXT, fim TEXT, duracao_min INTEGER,
        estado TEXT DEFAULT 'confirmada',
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO reservas VALUES(1,40,'Antonio Ricardo Sousa','CE-03-JP',809604,'MOVVI 3 (Interna Branca)','2026-07-12T19:00:00','2026-07-12T20:00:00',60,'confirmada','2026-07-11 23:45:43');
INSERT INTO reservas VALUES(2,40,'Antonio Ricardo Sousa','CE-03-JP',809604,'MOVVI 3 (Interna Branca)','2026-07-12T14:30:00','2026-07-12T15:30:00',60,'confirmada','2026-07-11 23:48:03');
INSERT INTO reservas VALUES(3,40,'Antonio Ricardo Sousa','CE-03-JP',519075,'MOVVI 4 (Interna Preta)','2026-07-12T17:00:00','2026-07-12T18:00:00',60,'confirmada','2026-07-11 23:49:38');
INSERT INTO reservas VALUES(4,40,'Antonio Ricardo Sousa','CE-03-JP',519075,'MOVVI 4 (Interna Preta)','2026-07-12T11:00:00','2026-07-12T12:00:00',60,'confirmada','2026-07-11 23:50:58');
INSERT INTO reservas VALUES(5,40,'Antonio Ricardo Sousa','CE-03-JP',519075,'MOVVI 4 (Interna Preta)','2026-07-12T13:30:00','2026-07-12T14:30:00',60,'confirmada','2026-07-11 23:51:49');
COMMIT;
