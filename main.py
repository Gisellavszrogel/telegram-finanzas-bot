import os
import logging
import psycopg2
from telegram.ext import Application, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

# Variables de entorno (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# Handler para registrar gastos/ingresos
async def save_expense(update, context):
    user_text = update.message.text

    try:
        # Suponemos que el usuario manda: fecha, monto, tipo, categoria
        # Ejemplo: 2025-10-01, 15000, comida, gasto
        parts = [p.strip() for p in user_text.split(",")]

        if len(parts) != 4:
            await update.message.reply_text("⚠️ Formato incorrecto. Usa: fecha, monto, tipo_gasto, categoria")
            return

        fecha, monto, tipo_gasto, categoria = parts

        # Conexión a DB
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cur = conn.cursor()

        # Insertar datos
        cur.execute(
            "INSERT INTO finanzas (fecha, monto, tipo_gasto, categoria) VALUES (%s, %s, %s, %s)",
            (fecha, monto, tipo_gasto, categoria)
        )

        conn.commit()
        cur.close()
        conn.close()

        # Responder
        await update.message.reply_text(
            f"✅ Registrado: {monto} en {tipo_gasto} como {categoria} el {fecha}"
        )

    except Exception as e:
        logging.error(f"❌ Error guardando gasto: {e}")
        await update.message.reply_text("⚠️ No pude guardar el gasto en la base de datos.")

# Configuración del bot
if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_expense))
    app.run_polling()
