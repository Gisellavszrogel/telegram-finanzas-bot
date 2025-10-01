import os
import logging
import psycopg2
from telegram.ext import Application, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

# Variables de entorno (en Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# Función que maneja los mensajes (handler)
async def save_message(update, context):
    user = update.message.from_user.first_name
    text = update.message.text

    # Abrir conexión cada vez que entra un mensaje
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Asegurar que la tabla exista
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (
            id SERIAL PRIMARY KEY,
            usuario TEXT,
            mensaje TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Guardar el mensaje
    cur.execute(
        "INSERT INTO mensajes (usuario, mensaje) VALUES (%s, %s)",
        (user, text)
    )
    conn.commit()

    # Cerrar conexión
    cur.close()
    conn.close()

    # Responder en Telegram
    await update.message.reply_text(
        f"✅ Hola {user}, registré tu mensaje: {text}"
    )

# Configuración del bot
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_message))

if __name__ == "__main__":
    app.run_polling()
