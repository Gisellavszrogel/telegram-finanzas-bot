import os
import logging
import psycopg2
from datetime import datetime
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_PUBLIC_URL")  # usa el PUBLIC_URL para conexiones externas

# Estados de la conversación
FECHA, MONTO, TIPO_GASTO, CATEGORIA, BANCO, DESCRIPCION, METODO_PAGO = range(7)

# Botones predefinidos
TIPOS_GASTO = [["Comida", "Transporte", "Vivienda"],
               ["Educación", "Ocio", "Salud"]]
CATEGORIAS = [["Gasto", "Ingreso"]]
METODOS_PAGO = [["Tarjeta Crédito", "Tarjeta Débito", "Inversión"]]

# --- Helpers ---
def parse_fecha_ddmmyyyy(txt: str) -> str:
    """Convierte DD-MM-YYYY a YYYY-MM-DD (ISO para Postgres)."""
    return datetime.strptime(txt.strip(), "%d-%m-%Y").strftime("%Y-%m-%d")

def parse_monto(txt: str) -> float:
    """Normaliza monto en distintos formatos a float."""
    s = txt.strip().replace("$", "").replace(" ", "")
    if "," in s and s.count(",") == 1 and (("." in s and s.rfind(".") < s.rfind(",")) or "." not in s):
        s = s.replace(".", "").replace(",", ".")
    return float(s)

def insert_into_db(data):
    conn = psycopg2.connect(DB_URL, sslmode="require")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS finanzas (
            id SERIAL PRIMARY KEY,
            fecha DATE,
            monto REAL,
            tipo_gasto TEXT,
            categoria TEXT,
            banco TEXT,
            descripcion TEXT,
            metodo_pago TEXT,
            creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.execute(
        "INSERT INTO finanzas (fecha, monto, tipo_gasto, categoria, banco, descripcion, metodo_pago) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (data["fecha"], data["monto"], data["tipo_gasto"], data["categoria"], data["banco"], data["descripcion"], data["metodo_pago"])
    )
    conn.commit()
    cur.close()
    conn.close()

# --- Flujo de conversación ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Ingresa la fecha (DD-MM-YYYY):")
    return FECHA

async def fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["fecha"] = parse_fecha_ddmmyyyy(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Formato inválido. Usa DD-MM-YYYY (ej: 02-10-2025).")
        return FECHA
    await update.message.reply_text("💰 Ingresa el monto:")
    return MONTO

async def monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["monto"] = parse_monto(update.message.text)
    except Exception:
        await update.message.reply_text("❌ Monto inválido. Ej: 15000 | 15.000,50 | 15000.50")
        return MONTO
    await update.message.reply_text(
        "🏷️ Selecciona el tipo de gasto:",
        reply_markup=ReplyKeyboardMarkup(TIPOS_GASTO, one_time_keyboard=True)
    )
    return TIPO_GASTO

async def tipo_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tipo_gasto"] = update.message.text
    await update.message.reply_text(
        "¿Es gasto o ingreso?",
        reply_markup=ReplyKeyboardMarkup(CATEGORIAS, one_time_keyboard=True)
    )
    return CATEGORIA

async def categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["categoria"] = update.message.text
    await update.message.reply_text("🏦 Ingresa el banco:", reply_markup=ReplyKeyboardRemove())
    return BANCO

async def banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["banco"] = update.message.text
    await update.message.reply_text("📝 Ingresa una descripción (opcional):")
    return DESCRIPCION

async def descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = update.message.text
    await update.message.reply_text(
        "💳 Selecciona el método de pago:",
        reply_markup=ReplyKeyboardMarkup(METODOS_PAGO, one_time_keyboard=True)
    )
    return METODO_PAGO

async def metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["metodo_pago"] = update.message.text

    # Guardar en DB
    insert_into_db(context.user_data)

    resumen = (
        f"✅ Registrado:\n"
        f"📅 Fecha: {datetime.strptime(context.user_data['fecha'], '%Y-%m-%d').strftime('%d-%m-%Y')}\n"
        f"💰 Monto: {context.user_data['monto']}\n"
        f"🏷️ Tipo de gasto: {context.user_data['tipo_gasto']}\n"
        f"📌 Categoría: {context.user_data['categoria']}\n"
        f"🏦 Banco: {context.user_data['banco']}\n"
        f"📝 Descripción: {context.user_data['descripcion']}\n"
        f"💳 Método de pago: {context.user_data['metodo_pago']}"
    )

    await update.message.reply_text(resumen, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Cancelar
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Registro cancelado.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- MAIN ---
if __name__ == "__main__":
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nuevo", start)],
        states={
            FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, fecha)],
            MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, monto)],
            TIPO_GASTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, tipo_gasto)],
            CATEGORIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, categoria)],
            BANCO: [MessageHandler(filters.TEXT & ~filters.COMMAND, banco)],
            DESCRIPCION: [MessageHandler(filters.TEXT & ~filters.COMMAND, descripcion)],
            METODO_PAGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, metodo_pago)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()
