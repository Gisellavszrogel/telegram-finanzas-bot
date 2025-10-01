import os
import psycopg2

DB_URL = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(DB_URL, sslmode="require")
    cur = conn.cursor()
    cur.execute("SELECT version();")
    record = cur.fetchone()
    print("✅ Conectado a Postgres:", record)

    # Crear tabla si no existe
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (
            id SERIAL PRIMARY KEY,
            usuario TEXT,
            mensaje TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    print("✅ Tabla lista")
    cur.close()
    conn.close()
except Exception as e:
    print("❌ Error conectando a Postgres:", e)
