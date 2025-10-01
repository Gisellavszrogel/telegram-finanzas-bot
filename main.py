import os
import logging
import psycopg2
from telegram.ext import Application, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

# Variables de entorno (las pondremos en Railway después)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# Conexión a Postgres
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Función para manejar mensajes
async def save_message(update, context):
    user = update.message.from_user.first_name
    text = update.message.text

    # Guardar en DB
    cur.execute(
        "INSERT INTO mensajes (usuario, mensaje) VALUES (%s, %s)",
        (user, text)
    )
    conn.commit()

    # Responder al usuario
    await update.message.reply_text(
        f"✅ Hola {user}, registré tu mensaje: {text}"
    )

# Configuración del bot
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_message))

if __name__ == "__main__":
    app.run_polling()
