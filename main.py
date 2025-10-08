import os
import logging
import psycopg2
from datetime import datetime
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import base64
import io

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_URL = os.getenv("DATABASE_PUBLIC_URL")

# Estados de la conversación
MENU, ESPERANDO_FOTO = range(2)
FECHA, MONTO, TIPO_GASTO, CATEGORIA, BANCO, DESCRIPCION, METODO_PAGO = range(2, 9)

# Botones predefinidos
MENU_PRINCIPAL = [
    ['🖋 Ingresar manualmente'],
    ['📸 Subir boleta (foto)']
]
TIPOS_GASTO = [["Comida", "Transporte", "Vivienda"],
               ["Educación", "Ocio", "Salud"]]
CATEGORIAS = [["Gasto", "Ingreso"]]
METODOS_PAGO = [["Tarjeta Crédito", "Tarjeta Débito", "Inversión"]]

# =============================================================================
# HELPERS
# =============================================================================

def parse_fecha_ddmmyyyy(txt: str) -> str:
    """Convierte DD-MM-YYYY a YYYY-MM-DD (ISO para Postgres)."""
    return datetime.strptime(txt.strip(), "%d-%m-%Y").strftime("%Y-%m-%d")

def parse_monto(txt: str) -> float:
    """Normaliza monto en distintos formatos a float."""
    s = txt.strip().replace("$", "").replace(" ", "")
    if "," in s and s.count(",") == 1 and (("." in s and s.rfind(".") < s.rfind(",")) or "." not in s):
        s = s.replace(".", "").replace(",", ".")
    return float(s)

def create_table():
    """Crea o actualiza la tabla finanzas con soporte para base64"""
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        
        cursor.execute("""
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
        
        columnas_nuevas = [
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'manual'",
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS image_path TEXT",
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS ocr_data JSONB",
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT",
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT",
            "ALTER TABLE finanzas ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP"
        ]
        
        for query in columnas_nuevas:
            try:
                cursor.execute(query)
            except Exception as e:
                logger.warning(f"⚠️ Columna ya existe: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("✅ Tabla verificada")
        
    except Exception as e:
        logger.error(f"❌ Error tabla: {e}")
        raise

def insert_into_db(data, status='manual'):
    """Inserta un registro en la base de datos"""
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO finanzas 
            (fecha, monto, tipo_gasto, categoria, banco, descripcion, metodo_pago, status) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data["fecha"], 
                data["monto"], 
                data["tipo_gasto"], 
                data["categoria"], 
                data["banco"], 
                data["descripcion"], 
                data["metodo_pago"],
                status
            )
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Guardado")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

# =============================================================================
# COMANDOS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    await update.message.reply_text(
        '👋 ¡Bienvenido a Mucho Derroche!\n\n'
        'Bot para registrar tus gastos.\n\n'
        'Usa /nuevo para registrar un gasto.'
    )

async def nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra menú principal"""
    keyboard = ReplyKeyboardMarkup(MENU_PRINCIPAL, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        '👋 ¿Cómo quieres registrar tu gasto?',
        reply_markup=keyboard
    )
    
    context.user_data["in_conversation"] = True
    return MENU

# =============================================================================
# MENÚ
# =============================================================================

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección del menú"""
    text = update.message.text.lower()
    
    if 'manual' in text or '🖋' in text:
        await update.message.reply_text(
            '📅 Fecha (DD-MM-YYYY):',
            reply_markup=ReplyKeyboardRemove()
        )
        return FECHA
    
    elif 'foto' in text or 'boleta' in text or '📸' in text:
        await update.message.reply_text(
            '📸 Envía la foto de tu boleta',
            reply_markup=ReplyKeyboardRemove()
        )
        return ESPERANDO_FOTO
    
    else:
        await update.message.reply_text('❌ Usa los botones')
        return MENU

# =============================================================================
# FOTO CON BASE64
# =============================================================================

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe foto y guarda como base64"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        # Descargar foto
        photo = update.message.photo[-1]
        file = await photo.get_file()
        
        foto_bytes = io.BytesIO()
        await file.download_to_memory(foto_bytes)
        foto_bytes.seek(0)
        
        # Convertir a base64
        foto_base64 = base64.b64encode(foto_bytes.read()).decode('utf-8')
        
        logger.info(f"📥 Foto en base64: {len(foto_base64)} chars")
        
        # Guardar en BD
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO finanzas (
                status, image_path, telegram_user_id, telegram_chat_id,
                metodo_pago, fecha, monto, tipo_gasto, categoria, banco, descripcion
            )
            VALUES (%s, %s, %s, %s, 'Por definir', CURRENT_DATE, 0, 'Pendiente', 'Pendiente', 'Pendiente', 'Procesando...')
            RETURNING id
        """, ('pending', foto_base64, user_id, chat_id))
        
        gasto_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"💾 ID={gasto_id}")
        
        # Encolar (IMPORTANTE: Importar aquí para evitar error de importación circular)
        try:
            from queue_manager import encolar_foto
            job = encolar_foto(gasto_id, foto_base64, chat_id, user_id)
            
            if job:
                await update.message.reply_text('⏳ *Procesando...*', parse_mode='Markdown')
            else:
                await update.message.reply_text('⚠️ Error al procesar')
        except ImportError:
            logger.warning("⚠️ queue_manager no disponible, procesando sin cola")
            await update.message.reply_text('⚠️ Sistema de colas no disponible')
        
        context.user_data["in_conversation"] = False
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        await update.message.reply_text('❌ Error')
        return ConversationHandler.END

# =============================================================================
# CALLBACKS
# =============================================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja botones"""
    query = update.callback_query
    await query.answer()
    
    try:
        parts = query.data.split('_')
        action = parts[0]
        
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        
        # CONFIRMAR GASTO
        if action == 'confirm':
            gasto_id = int(parts[1])
            cursor.execute("UPDATE finanzas SET status = 'confirmed' WHERE id = %s", (gasto_id,))
            conn.commit()
            await query.edit_message_text('✅ *Gasto guardado correctamente!*', parse_mode='Markdown')
        
        # CANCELAR
        elif action == 'cancel':
            gasto_id = int(parts[1])
            cursor.execute("DELETE FROM finanzas WHERE id = %s", (gasto_id,))
            conn.commit()
            await query.edit_message_text('🗑️ Gasto cancelado.')
        
        # SELECCIONAR MONTO (sin propina)
        elif action == 'monto' and parts[1] == 'sin':
            gasto_id = int(parts[2])
            monto = float(parts[3])
            cursor.execute("UPDATE finanzas SET monto = %s WHERE id = %s", (monto, gasto_id))
            conn.commit()
            await query.answer(f"✅ Registrado: ${monto:,.0f} (sin propina)")
        
        # SELECCIONAR MONTO (con propina)
        elif action == 'monto' and parts[1] == 'con':
            gasto_id = int(parts[2])
            monto = float(parts[3])
            cursor.execute("UPDATE finanzas SET monto = %s WHERE id = %s", (monto, gasto_id))
            conn.commit()
            await query.answer(f"✅ Registrado: ${monto:,.0f} (con propina)")
        
        # MONTO MANUAL
        elif action == 'monto' and parts[1] == 'manual':
            gasto_id = int(parts[2])
            await query.edit_message_text(
                f'💰 *Ingresa el monto que pagaste:*\n\nEscribe solo el número.',
                parse_mode='Markdown'
            )
            context.user_data['esperando_monto_manual'] = gasto_id
        
        # CATEGORÍA OK
        elif action == 'cat' and parts[1] == 'ok':
            await query.answer("✅ Categoría confirmada")
        
        # CAMBIAR CATEGORÍA
        elif action == 'cat' and parts[1] == 'change':
            gasto_id = int(parts[2])
            
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            keyboard = [
                [InlineKeyboardButton("Comida", callback_data=f"setcat_{gasto_id}_Comida"),
                 InlineKeyboardButton("Transporte", callback_data=f"setcat_{gasto_id}_Transporte")],
                [InlineKeyboardButton("Vivienda", callback_data=f"setcat_{gasto_id}_Vivienda"),
                 InlineKeyboardButton("Educación", callback_data=f"setcat_{gasto_id}_Educación")],
                [InlineKeyboardButton("Ocio", callback_data=f"setcat_{gasto_id}_Ocio"),
                 InlineKeyboardButton("Salud", callback_data=f"setcat_{gasto_id}_Salud")]
            ]
            
            await query.edit_message_text(
                '🏷️ *Selecciona la categoría correcta:*',
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # GUARDAR CATEGORÍA
        elif action == 'setcat':
            gasto_id = int(parts[1])
            categoria = '_'.join(parts[2:])  # Por si tiene espacios
            cursor.execute("UPDATE finanzas SET tipo_gasto = %s WHERE id = %s", (categoria, gasto_id))
            conn.commit()
            await query.answer(f"✅ Categoría: {categoria}")
            await query.edit_message_text(f'✅ Categoría actualizada a: *{categoria}*\n\nUsa los botones anteriores para confirmar.', parse_mode='Markdown')

        # EDITAR GASTO
        elif action == 'edit':
            gasto_id = int(parts[1])

            # Obtener datos actuales
            cursor.execute("SELECT monto, tipo_gasto, descripcion, fecha FROM finanzas WHERE id = %s", (gasto_id,))
            row = cursor.fetchone()

            if row:
                monto, tipo_gasto, descripcion, fecha = row

                keyboard = [
                    [InlineKeyboardButton("💰 Cambiar monto", callback_data=f"editmonto_{gasto_id}")],
                    [InlineKeyboardButton("🏷️ Cambiar categoría", callback_data=f"cat_change_{gasto_id}")],
                    [InlineKeyboardButton("📝 Cambiar descripción", callback_data=f"editdesc_{gasto_id}")],
                    [InlineKeyboardButton("📅 Cambiar fecha", callback_data=f"editfecha_{gasto_id}")],
                    [InlineKeyboardButton("✅ Guardar así", callback_data=f"confirm_{gasto_id}")]
                ]

                await query.edit_message_text(
                    f'✏️ *Editando gasto #{gasto_id}*\n\n'
                    f'💰 Monto: ${monto:,.0f}\n'
                    f'🏷️ Categoría: {tipo_gasto}\n'
                    f'📝 Descripción: {descripcion}\n'
                    f'📅 Fecha: {fecha}\n\n'
                    f'¿Qué quieres cambiar?',
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.answer("❌ Gasto no encontrado")

        # EDITAR MONTO
        elif action == 'editmonto':
            gasto_id = int(parts[1])
            await query.edit_message_text(
                f'💰 *Editar monto del gasto #{gasto_id}*\n\n'
                f'Escribe el nuevo monto (solo número):',
                parse_mode='Markdown'
            )
            context.user_data['esperando_monto_editar'] = gasto_id

        # EDITAR DESCRIPCIÓN
        elif action == 'editdesc':
            gasto_id = int(parts[1])
            await query.edit_message_text(
                f'📝 *Editar descripción del gasto #{gasto_id}*\n\n'
                f'Escribe la nueva descripción:',
                parse_mode='Markdown'
            )
            context.user_data['esperando_desc_editar'] = gasto_id

        # EDITAR FECHA
        elif action == 'editfecha':
            gasto_id = int(parts[1])
            await query.edit_message_text(
                f'📅 *Editar fecha del gasto #{gasto_id}*\n\n'
                f'Escribe la nueva fecha (DD-MM-YYYY):',
                parse_mode='Markdown'
            )
            context.user_data['esperando_fecha_editar'] = gasto_id

        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        await query.answer("❌ Error procesando")

# =============================================================================
# FLUJO MANUAL
# =============================================================================

async def fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["fecha"] = parse_fecha_ddmmyyyy(update.message.text)
        await update.message.reply_text("💰 Monto:")
        return MONTO
    except ValueError:
        await update.message.reply_text("❌ Formato inválido")
        return FECHA

async def monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["monto"] = parse_monto(update.message.text)
        await update.message.reply_text(
            "🏷️ Tipo:",
            reply_markup=ReplyKeyboardMarkup(TIPOS_GASTO, one_time_keyboard=True, resize_keyboard=True)
        )
        return TIPO_GASTO
    except:
        await update.message.reply_text("❌ Monto inválido")
        return MONTO

async def tipo_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tipo_gasto"] = update.message.text
    await update.message.reply_text(
        "Gasto o ingreso?",
        reply_markup=ReplyKeyboardMarkup(CATEGORIAS, one_time_keyboard=True, resize_keyboard=True)
    )
    return CATEGORIA

async def categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["categoria"] = update.message.text
    await update.message.reply_text("🏦 Banco:", reply_markup=ReplyKeyboardRemove())
    return BANCO

async def banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["banco"] = update.message.text
    await update.message.reply_text("📝 Descripción:")
    return DESCRIPCION

async def descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = update.message.text if update.message.text.lower() != 'ninguna' else "Sin descripción"
    await update.message.reply_text(
        "💳 Método:",
        reply_markup=ReplyKeyboardMarkup(METODOS_PAGO, one_time_keyboard=True, resize_keyboard=True)
    )
    return METODO_PAGO

async def metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["metodo_pago"] = update.message.text
    
    try:
        insert_into_db(context.user_data)
        await update.message.reply_text('✅ Guardado', reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.error(f"❌ {e}")
        await update.message.reply_text("❌ Error", reply_markup=ReplyKeyboardRemove())
    
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelado", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def handle_edicion_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la edición manual de campos (monto, descripción, fecha)"""
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()

        # EDITAR MONTO
        if 'esperando_monto_editar' in context.user_data:
            gasto_id = context.user_data.pop('esperando_monto_editar')
            try:
                nuevo_monto = parse_monto(update.message.text)
                cursor.execute("UPDATE finanzas SET monto = %s WHERE id = %s", (nuevo_monto, gasto_id))
                conn.commit()
                await update.message.reply_text(f'✅ Monto actualizado a ${nuevo_monto:,.0f}\n\nUsa /nuevo para otro gasto.')
            except:
                await update.message.reply_text('❌ Monto inválido')

        # EDITAR DESCRIPCIÓN
        elif 'esperando_desc_editar' in context.user_data:
            gasto_id = context.user_data.pop('esperando_desc_editar')
            nueva_desc = update.message.text
            cursor.execute("UPDATE finanzas SET descripcion = %s WHERE id = %s", (nueva_desc, gasto_id))
            conn.commit()
            await update.message.reply_text(f'✅ Descripción actualizada\n\nUsa /nuevo para otro gasto.')

        # EDITAR FECHA
        elif 'esperando_fecha_editar' in context.user_data:
            gasto_id = context.user_data.pop('esperando_fecha_editar')
            try:
                nueva_fecha = parse_fecha_ddmmyyyy(update.message.text)
                cursor.execute("UPDATE finanzas SET fecha = %s WHERE id = %s", (nueva_fecha, gasto_id))
                conn.commit()
                await update.message.reply_text(f'✅ Fecha actualizada\n\nUsa /nuevo para otro gasto.')
            except:
                await update.message.reply_text('❌ Fecha inválida (usa DD-MM-YYYY)')

        # MONTO MANUAL (del callback original)
        elif 'esperando_monto_manual' in context.user_data:
            gasto_id = context.user_data.pop('esperando_monto_manual')
            try:
                monto = parse_monto(update.message.text)
                cursor.execute("UPDATE finanzas SET monto = %s WHERE id = %s", (monto, gasto_id))
                conn.commit()
                await update.message.reply_text(f'✅ Monto registrado: ${monto:,.0f}\n\nUsa /nuevo para otro gasto.')
            except:
                await update.message.reply_text('❌ Monto inválido')

        else:
            await update.message.reply_text("👋 Usa /nuevo")

        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"❌ Error editando: {e}")
        await update.message.reply_text('❌ Error actualizando')

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para mensajes fuera de conversación"""
    await handle_edicion_manual(update, context)

# =============================================================================
# MAIN
# =============================================================================

def main():
    logger.info("🔄 Iniciando...")
    create_table()
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nuevo", nuevo)],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)],
            ESPERANDO_FOTO: [MessageHandler(filters.PHOTO, recibir_foto)],
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
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))
    
    logger.info("🚀 Bot iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
