import os
import logging
import psycopg2
import time
from telegram.ext import Application, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

# Variables de entorno (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# Función auxiliar: crear tabla si no existe
def ensure_table():
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensajes (
                id SERIAL PRIMARY KEY,
                usuario TEXT,
                mensaje TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logging.info("✅ Tabla 'mensajes' verificada/creada")
    except Exception as e:
        logging.error(f"❌ Error creando tabla: {e}")

# Handler de mensajes
async def save_message(update, context):
    user = update.message.from_user.first_name
    text = update.message.text

    try:
        # Conexión a la DB con SSL
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cur = conn.cursor()

        # Debug: comprobar conexión
        cur.execute("SELECT 1;")
        logging.info("✅ Conexión a DB exitosa")

        # Insertar mensaje
        cur.execute(
            "INSERT INTO mensajes (usuario, mensaje) VALUES (%s, %s)",
            (user, text)
        )
        conn.commit()

        cur.close()
        conn.close()

        await update.message.reply_text(f"✅ Guardado en DB: {text}")
    except Exception as e:
        logging.error(f"❌ Error guardando mensaje: {e}")
        await update.message.reply_text("⚠️ No pude guardar en la base de datos.")

# Configuración del bot con retry para DB
if __name__ == "__main__":
    # Reintenta conexión inicial a la DB
    for i in range(5):
        try:
            ensure_table()
            break
        except Exception as e:
            logging.error(f"❌ Intento {i+1} fallido: {e}")
            time.sleep(5)

    # Inicia el bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_message))
    app.run_polling()
