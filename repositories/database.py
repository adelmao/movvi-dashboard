import sqlite3

from config.settings import DB_PATH


class Database:

    @staticmethod
    def connect():
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


def get_db():
    return Database.connect()
