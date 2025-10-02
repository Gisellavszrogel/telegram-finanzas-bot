import os
import logging
import psycopg2
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, MessageHandler, filters, CommandHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)

logging.basicConfig(level=logging.INFO)

# Variables de entorno (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_PUBLIC_URL")  # importante usar la pública

# Estados de la conversación
FECHA, MONTO, TIPO, CATEGORIA, BANCO, DESCRIPCION, METODO = range(7)

# Función auxiliar: crear tabla si no existe
def ensure_table():
    conn = psycopg2.connect(DB_URL, sslmode="require")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS finanzas (
            id SERIAL PRIMARY KEY,
            fecha DATE,
            monto REAL,
            tipo TEXT,
            categoria TEXT,
            banco TEXT,
            descripcion TEXT,
            metodo_pago TEXT,
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logging.info("✅ Tabla 'finanzas' verificada/creada")

# --- Flujo de conversación ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Ingresa la fecha del movimiento (YYYY-MM-DD):")
    return FECHA

async def fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fecha"] = update.message.text
    await update.message.reply_text("💲 Ingresa el monto:")
    return MONTO

async def monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["monto"] = float(update.message.text)

    # Botones para gasto/ingreso
    keyboard = [
        [InlineKeyboardButton("📉 Gasto", callback_data="Gasto")],
        [InlineKeyboardButton("📈 Ingreso", callback_data="Ingreso")]
    ]
    await update.message.reply_text("📌 ¿Es gasto o ingreso?", reply_markup=InlineKeyboardMarkup(keyboard))
    return TIPO

async def tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["tipo"] = query.data

    # Botones de categorías
    keyboard = [
        [InlineKeyboardButton("🍔 Comida", callback_data="Comida"),
         InlineKeyboardButton("🚌 Transporte", callback_data="Transporte")],
        [InlineKeyboardButton("🏠 Vivienda", callback_data="Vivienda"),
         InlineKeyboardButton("📚 Educación", callback_data="Educación")],
        [InlineKeyboardButton("🎉 Ocio", callback_data="Ocio"),
         InlineKeyboardButton("🩺 Salud", callback_data="Salud")]
    ]
    await query.edit_message_text("📂 Selecciona la categoría:")
    await query.message.reply_text("📂 Selecciona la categoría:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CATEGORIA

async def categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["categoria"] = query.data

    await query.edit_message_text("🏦 Ingresa el banco:")
    return BANCO

async def banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["banco"] = update.message.text
    await update.message.reply_text("📝 Ingresa una descripción:")
    return DESCRIPCION

async def descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = update.message.text

    # Botones método de pago
    keyboard = [
        [InlineKeyboardButton("💳 Crédito", callback_data="Crédito")],
        [InlineKeyboardButton("💳 Débito", callback_data="Débito")],
        [InlineKeyboardButton("📈 Inversión", callback_data="Inversión")]
    ]
    await update.message.reply_text("💳 Selecciona el método de pago:", reply_markup=InlineKeyboardMarkup(keyboard))
    return METODO

async def metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["metodo_pago"] = query.data

    # Guardamos en DB
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO finanzas (fecha, monto, tipo, categoria, banco, descripcion, metodo_pago)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            context.user_data["fecha"],
            context.user_data["monto"],
            context.user_data["tipo"],
            context.user_data["categoria"],
            context.user_data["banco"],
            context.user_data["descripcion"],
            context.user_data["metodo_pago"]
        ))
        conn.commit()
        cur.close()
        conn.close()

        await query.edit_message_text(
            text=f"✅ Registro guardado:\n"
                 f"📅 {context.user_data['fecha']}\n"
                 f"💲 {context.user_data['monto']}\n"
                 f"📌 {context.user_data['tipo']}\n"
                 f"📂 {context.user_data['categoria']}\n"
                 f"🏦 {context.user_data['banco']}\n"
                 f"📝 {context.user_data['descripcion']}\n"
                 f"💳 {context.user_data['metodo_pago']}"
        )
    except Exception as e:
        await query.edit_message_text(f"⚠️ Error guardando en DB: {e}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Operación cancelada.")
    return ConversationHandler.END

# --- MAIN ---
if __name__ == "__main__":
    for i in range(5):
        try:
            ensure_table()
            break
        except Exception as e:
            logging.error(f"❌ Intento {i+1} fallido: {e}")
            time.sleep(5)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nuevo", start)],
        states={
            FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, fecha)],
            MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, monto)],
            TIPO: [CallbackQueryHandler(tipo)],
            CATEGORIA: [CallbackQueryHandler(categoria)],
            BANCO: [MessageHandler(filters.TEXT & ~filters.COMMAND, banco)],
            DESCRIPCION: [MessageHandler(filters.TEXT & ~filters.COMMAND, descripcion)],
            METODO: [CallbackQueryHandler(metodo_pago)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()
