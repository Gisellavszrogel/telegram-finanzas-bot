import os
import logging
import psycopg2
import time
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, MessageHandler, filters, CommandHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)

logging.basicConfig(level=logging.INFO)

# Variables de entorno (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")  # usa interna si existe, si no la pública

# Estados de la conversación
FECHA, MONTO, TIPO, CATEGORIA, BANCO, DESCRIPCION, METODO = range(7)

# --- Utilidades ---
def get_conn():
    return psycopg2.connect(DB_URL, sslmode="require")

def ensure_table():
    conn = get_conn()
    cur = conn.cursor()
    # Crea tabla si no existe
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
        );
    """)
    # Asegura columnas (por si existía la tabla sin alguna)
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS fecha DATE;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS monto REAL;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS tipo TEXT;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS categoria TEXT;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS banco TEXT;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS descripcion TEXT;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS metodo_pago TEXT;")
    cur.execute("ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
    conn.commit()
    cur.close()
    conn.close()
    logging.info("✅ Tabla 'finanzas' lista")

def parse_fecha_ddmmyyyy(txt: str) -> str:
    """
    Recibe 'DD-MM-YYYY' y devuelve 'YYYY-MM-DD' (ISO) para Postgres.
    Lanza ValueError si el formato no es válido.
    """
    dt = datetime.strptime(txt.strip(), "%d-%m-%Y")
    return dt.strftime("%Y-%m-%d")

def parse_monto(txt: str) -> float:
    """
    Acepta formatos '12.345,67' o '12345.67' o '12345'.
    Normaliza a float.
    """
    s = txt.strip().replace("$", "").replace(" ", "")
    # Si tiene coma decimal europea, conviértela
    if "," in s and s.count(",") == 1 and (("." in s and s.rfind(".") < s.rfind(",")) or "." not in s):
        s = s.replace(".", "").replace(",", ".")
    return float(s)

# --- Flujo ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Ingresa la fecha (DD-MM-YYYY):")
    return FECHA

async def fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        iso = parse_fecha_ddmmyyyy(update.message.text)
        context.user_data["fecha"] = iso
    except ValueError:
        await update.message.reply_text("❌ Formato inválido. Usa DD-MM-YYYY (ej: 02-10-2025).")
        return FECHA

    await update.message.reply_text("💲 Ingresa el monto:")
    return MONTO

async def monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["monto"] = parse_monto(update.message.text)
    except Exception:
        await update.message.reply_text("❌ Monto inválido. Ejemplos válidos: 15000 | 15,000.50 | 15.000,50")
        return MONTO

    # Botones: Gasto/Ingreso
    keyboard = [
        [InlineKeyboardButton("📉 Gasto", callback_data="Gasto")],
        [InlineKeyboardButton("📈 Ingreso", callback_data="Ingreso")]
    ]
    await update.message.reply_text(
        "📌 ¿Es gasto o ingreso?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TIPO

async def tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice not in ("Gasto", "Ingreso"):
        await query.answer("Selecciona una opción válida.", show_alert=False)
        return TIPO

    context.user_data["tipo"] = choice

    # Botones de categorías
    keyboard = [
        [InlineKeyboardButton("🍔 Comida", callback_data="Comida"),
         InlineKeyboardButton("🚌 Transporte", callback_data="Transporte")],
        [InlineKeyboardButton("🏠 Vivienda", callback_data="Vivienda"),
         InlineKeyboardButton("📚 Educación", callback_data="Educación")],
        [InlineKeyboardButton("🎉 Ocio", callback_data="Ocio"),
         InlineKeyboardButton("🩺 Salud", callback_data="Salud")]
    ]
    # Edita el mensaje anterior y envía el siguiente con teclado
    await query.edit_message_text("📂 Selecciona la categoría:")
    await query.message.reply_text(
        "📂 Selecciona la categoría:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CATEGORIA

async def categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    categoria = query.data
    categorias_validas = {"Comida", "Transporte", "Vivienda", "Educación", "Ocio", "Salud"}
    if categoria not in categorias_validas:
        await query.answer("Selecciona una categoría válida.", show_alert=False)
        return CATEGORIA

    context.user_data["categoria"] = categoria

    # Pide banco como texto
    await query.edit_message_text("🏦 Ingresa el banco:")
    return BANCO

async def banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["banco"] = update.message.text.strip()
    await update.message.reply_text("📝 Ingresa una descripción:")
    return DESCRIPCION

async def descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = update.message.text.strip()

    # Botones método de pago
    keyboard = [
        [InlineKeyboardButton("💳 Crédito", callback_data="Crédito")],
        [InlineKeyboardButton("💳 Débito", callback_data="Débito")],
        [InlineKeyboardButton("📈 Inversión", callback_data="Inversión")]
    ]
    await update.message.reply_text(
        "💳 Selecciona el método de pago:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return METODO

async def metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    metodo = query.data
    if metodo not in ("Crédito", "Débito", "Inversión"):
        await query.answer("Selecciona un método válido.", show_alert=False)
        return METODO

    context.user_data["metodo_pago"] = metodo

    # Guardar en DB
    conn = get_conn()
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

    # Resumen
    await query.edit_message_text(
        text=(
            "✅ Registro guardado:\n"
            f"📅 {datetime.strptime(context.user_data['fecha'], '%Y-%m-%d').strftime('%d-%m-%Y')}\n"
            f"💲 {context.user_data['monto']}\n"
            f"📌 {context.user_data['tipo']}\n"
            f"📂 {context.user_data['categoria']}\n"
            f"🏦 {context.user_data['banco']}\n"
            f"📝 {context.user_data['descripcion']}\n"
            f"💳 {context.user_data['metodo_pago']}"
        )
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Operación cancelada.")
    return ConversationHandler.END

# --- MAIN ---
if __name__ == "__main__":
    # Asegurar tabla (y columnas) al arrancar
    for i in range(3):
        try:
            ensure_table()
            break
        except Exception as e:
            logging.error(f"❌ ensure_table intento {i+1} fallido: {e}")
            time.sleep(3)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nuevo", start)],
        states={
            FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, fecha)],
            MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, monto)],
            TIPO: [CallbackQueryHandler(tipo, pattern=r"^(Gasto|Ingreso)$")],
            CATEGORIA: [CallbackQueryHandler(categoria, pattern=r"^(Comida|Transporte|Vivienda|Educación|Ocio|Salud)$")],
            BANCO: [MessageHandler(filters.TEXT & ~filters.COMMAND, banco)],
            DESCRIPCION: [MessageHandler(filters.TEXT & ~filters.COMMAND, descripcion)],
            METODO: [CallbackQueryHandler(metodo_pago, pattern=r"^(Crédito|Débito|Inversión)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()
