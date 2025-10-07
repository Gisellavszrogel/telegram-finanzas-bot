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
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from queue_manager import encolar_foto
import json

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
    """
    Crea o actualiza la tabla finanzas con soporte para colas y OCR
    """
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        
        # Crear tabla base si no existe
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
        
        # Agregar nuevas columnas para el sistema de colas
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
                logger.info(f"✅ Ejecutado: {query[:60]}...")
            except Exception as e:
                logger.warning(f"⚠️ Columna ya existe o error menor: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("✅ Tabla finanzas verificada/actualizada correctamente")
        
    except Exception as e:
        logger.error(f"❌ Error al crear/actualizar tabla: {e}")
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
        logger.info("✅ Registro guardado en BD")
    except Exception as e:
        logger.error(f"❌ Error guardando en BD: {e}")
        raise

# =============================================================================
# COMANDO /nuevo - MENÚ PRINCIPAL
# =============================================================================

async def nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el menú principal para elegir método de registro"""
    keyboard = ReplyKeyboardMarkup(MENU_PRINCIPAL, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        '👋 ¡Hola! ¿Cómo quieres registrar tu gasto?',
        reply_markup=keyboard
    )
    
    context.user_data["in_conversation"] = True
    return MENU

# =============================================================================
# MANEJADOR DEL MENÚ PRINCIPAL
# =============================================================================

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección del menú principal"""
    text = update.message.text.lower()
    
    # Opción 1: Ingreso manual
    if 'manual' in text or '🖋' in text:
        await update.message.reply_text(
            '📅 Ingresa la fecha del gasto (DD-MM-YYYY):',
            reply_markup=ReplyKeyboardRemove()
        )
        return FECHA
    
    # Opción 2: Subir foto
    elif 'foto' in text or 'boleta' in text or '📸' in text:
        await update.message.reply_text(
            '📸 *Envía la foto de tu boleta*\n\n'
            'Asegúrate de que se vea claramente:\n'
            '• El monto\n'
            '• La fecha\n'
            '• El nombre del comercio\n\n'
            '_Procesaremos la imagen automáticamente._',
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )
        return ESPERANDO_FOTO
    
    # Opción inválida
    else:
        await update.message.reply_text(
            '❌ Opción no válida. Por favor usa los botones del menú.',
            reply_markup=ReplyKeyboardMarkup(MENU_PRINCIPAL, one_time_keyboard=True, resize_keyboard=True)
        )
        return MENU

# =============================================================================
# FLUJO DE FOTO (NUEVO)
# =============================================================================

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Recibe la foto del usuario y la encola para procesamiento asíncrono
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    try:
        # 1. Descargar la foto
        photo = update.message.photo[-1]  # Tomar la de mayor resolución
        file = await photo.get_file()
        
        # 2. Guardar localmente
        os.makedirs('uploads', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        image_path = f'uploads/{user_id}_{timestamp}.jpg'
        await file.download_to_drive(image_path)
        
        logger.info(f"📥 Foto guardada: {image_path}")
        
        # 3. Crear registro en BD con status='pending'
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        cursor.execute("""
    INSERT INTO finanzas (
        status, image_path, telegram_user_id, telegram_chat_id,
        metodo_pago, fecha, monto, tipo_gasto, categoria, banco, descripcion
    )
    VALUES (%s, %s, %s, %s, 'Por definir', CURRENT_DATE, 0, 'Pendiente', 'Pendiente', 'Pendiente', 'Procesando boleta...')
    RETURNING id
""", ('pending', image_path, user_id, chat_id))
        
        gasto_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"💾 Registro creado con ID={gasto_id}, status=pending")
        
        # 4. Encolar para procesamiento en background
        job = encolar_foto(gasto_id, image_path, chat_id, user_id)
        
        if job:
            await update.message.reply_text(
                '⏳ *Tu boleta está siendo procesada...*\n\n'
                'Te notificaré en unos segundos con los datos extraídos.\n'
                'Puedes seguir usando el bot normalmente.',
                parse_mode='Markdown'
            )
            logger.info(f"✅ Job encolado exitosamente: {job.id}")
        else:
            # Si falla el encolado, ofrecer alternativa
            await update.message.reply_text(
                '⚠️ *Hubo un problema al procesar tu boleta.*\n\n'
                '¿Quieres ingresarla manualmente?',
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup([['🖋 Sí, ingresar manual']], one_time_keyboard=True)
            )
            logger.warning(f"❌ No se pudo encolar el job para gasto_id={gasto_id}")
        
        context.user_data["in_conversation"] = False
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Error procesando foto: {e}", exc_info=True)
        await update.message.reply_text(
            '❌ *Error al procesar la imagen*\n\n'
            'Por favor, intenta de nuevo o usa el ingreso manual con /nuevo',
            parse_mode='Markdown'
        )
        context.user_data["in_conversation"] = False
        return ConversationHandler.END

# =============================================================================
# CALLBACKS DE CONFIRMACIÓN (NUEVO)
# =============================================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja los botones inline de confirmación enviados por el worker
    """
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data
        action, gasto_id = data.split('_', 1)
        gasto_id = int(gasto_id)
        
        conn = psycopg2.connect(DB_URL, sslmode="require")
        cursor = conn.cursor()
        
        if action == 'confirm':
            # Confirmar y guardar definitivamente
            cursor.execute("""
                UPDATE finanzas 
                SET status = 'confirmed'
                WHERE id = %s
            """, (gasto_id,))
            conn.commit()
            
            await query.edit_message_text(
                '✅ *¡Gasto guardado correctamente!*\n\n'
                'Puedes registrar otro con /nuevo',
                parse_mode='Markdown'
            )
            logger.info(f"✅ Gasto {gasto_id} confirmado por usuario")
        
        elif action == 'edit':
            # Cargar datos para edición manual
            cursor.execute("""
                SELECT fecha, monto, tipo_gasto, categoria, descripcion 
                FROM finanzas 
                WHERE id = %s
            """, (gasto_id,))
            
            datos = cursor.fetchone()
            
            if datos:
                context.user_data['gasto_id_editando'] = gasto_id
                context.user_data['fecha'] = datos[0].strftime('%Y-%m-%d') if datos[0] else None
                context.user_data['monto'] = float(datos[1]) if datos[1] else None
                context.user_data['tipo_gasto'] = datos[2]
                context.user_data['categoria'] = datos[3]
                context.user_data['descripcion'] = datos[4]
                
                await query.edit_message_text(
                    '✏️ *Modo de edición activado*\n\n'
                    f'Datos actuales:\n'
                    f'📅 Fecha: {datos[0] if datos[0] else "No detectada"}\n'
                    f'💰 Monto: ${datos[1] if datos[1] else "No detectado"}\n\n'
                    'Usa /nuevo para modificar los campos que desees.',
                    parse_mode='Markdown'
                )
                logger.info(f"✏️ Gasto {gasto_id} en modo edición")
            else:
                await query.edit_message_text('❌ No se encontraron datos para editar.')
        
        elif action == 'cancel':
            # Eliminar el registro
            cursor.execute("DELETE FROM finanzas WHERE id = %s", (gasto_id,))
            conn.commit()
            
            await query.edit_message_text(
                '🗑️ Gasto cancelado y eliminado.\n\n'
                'Usa /nuevo para registrar otro.',
                parse_mode='Markdown'
            )
            logger.info(f"🗑️ Gasto {gasto_id} cancelado por usuario")
        
        elif action == 'retry':
            # Reencolar el procesamiento
            cursor.execute("SELECT image_path FROM finanzas WHERE id = %s", (gasto_id,))
            result = cursor.fetchone()
            
            if result:
                image_path = result[0]
                chat_id = query.message.chat_id
                user_id = query.from_user.id
                
                # Actualizar status a pending
                cursor.execute("""
                    UPDATE finanzas 
                    SET status = 'pending', processed_at = NULL 
                    WHERE id = %s
                """, (gasto_id,))
                conn.commit()
                
                # Reencolar
                job = encolar_foto(gasto_id, image_path, chat_id, user_id)
                
                if job:
                    await query.edit_message_text('🔄 *Reintentando procesamiento...*', parse_mode='Markdown')
                    logger.info(f"🔄 Gasto {gasto_id} reencolado")
                else:
                    await query.edit_message_text('❌ No se pudo reintentar. Usa ingreso manual.')
            else:
                await query.edit_message_text('❌ No se encontró la imagen para reintentar.')
        
        elif action == 'manual':
            await query.edit_message_text(
                '📝 Usa /nuevo para iniciar el ingreso manual.',
                parse_mode='Markdown'
            )
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error en callback_handler: {e}", exc_info=True)
        await query.edit_message_text('❌ Error al procesar la acción. Intenta de nuevo.')

# =============================================================================
# FLUJO MANUAL (ORIGINAL CON MEJORAS)
# =============================================================================

async def fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["fecha"] = parse_fecha_ddmmyyyy(update.message.text)
        await update.message.reply_text("💰 Ingresa el monto:")
        return MONTO
    except ValueError:
        await update.message.reply_text(
            "❌ Formato inválido. Usa DD-MM-YYYY\n"
            "Ejemplo: 06-10-2025"
        )
        return FECHA

async def monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["monto"] = parse_monto(update.message.text)
        await update.message.reply_text(
            "🏷️ Selecciona el tipo de gasto:",
            reply_markup=ReplyKeyboardMarkup(TIPOS_GASTO, one_time_keyboard=True, resize_keyboard=True)
        )
        return TIPO_GASTO
    except Exception:
        await update.message.reply_text(
            "❌ Monto inválido.\n"
            "Ejemplos válidos: 15000 | 15.000 | 15000.50"
        )
        return MONTO

async def tipo_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tipo_gasto"] = update.message.text
    await update.message.reply_text(
        "¿Es gasto o ingreso?",
        reply_markup=ReplyKeyboardMarkup(CATEGORIAS, one_time_keyboard=True, resize_keyboard=True)
    )
    return CATEGORIA

async def categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["categoria"] = update.message.text
    await update.message.reply_text(
        "🏦 Ingresa el nombre del banco:",
        reply_markup=ReplyKeyboardRemove()
    )
    return BANCO

async def banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["banco"] = update.message.text
    await update.message.reply_text(
        "📝 Ingresa una descripción (o escribe 'ninguna' para omitir):"
    )
    return DESCRIPCION

async def descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text
    context.user_data["descripcion"] = desc if desc.lower() != 'ninguna' else "Sin descripción"
    
    await update.message.reply_text(
        "💳 Selecciona el método de pago:",
        reply_markup=ReplyKeyboardMarkup(METODOS_PAGO, one_time_keyboard=True, resize_keyboard=True)
    )
    return METODO_PAGO

async def metodo_pago(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["metodo_pago"] = update.message.text

    # Guardar en BD
    try:
        insert_into_db(context.user_data, status='manual')
        
        # Resumen
        resumen = (
            f"✅ *Gasto registrado exitosamente*\n\n"
            f"📅 Fecha: {datetime.strptime(context.user_data['fecha'], '%Y-%m-%d').strftime('%d-%m-%Y')}\n"
            f"💰 Monto: ${context.user_data['monto']:,.2f}\n"
            f"🏷️ Tipo: {context.user_data['tipo_gasto']}\n"
            f"📌 Categoría: {context.user_data['categoria']}\n"
            f"🏦 Banco: {context.user_data['banco']}\n"
            f"📝 Descripción: {context.user_data['descripcion']}\n"
            f"💳 Método: {context.user_data['metodo_pago']}\n\n"
            f"Usa /nuevo para registrar otro gasto."
        )
        
        await update.message.reply_text(
            resumen,
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()
        )
        logger.info("✅ Gasto manual guardado correctamente")
        
    except Exception as e:
        logger.error(f"❌ Error guardando gasto manual: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Error al guardar el gasto. Intenta de nuevo con /nuevo",
            reply_markup=ReplyKeyboardRemove()
        )
    
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

# =============================================================================
# CANCELAR CONVERSACIÓN
# =============================================================================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_conversation"] = False
    await update.message.reply_text(
        "❌ Registro cancelado.\n\nUsa /nuevo para empezar de nuevo.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# =============================================================================
# MENSAJES FUERA DE CONTEXTO
# =============================================================================

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes fuera del flujo de conversación"""
    if not context.user_data.get("in_conversation", False):
        await update.message.reply_text(
            "👋 ¡Hola! Usa /nuevo para registrar un gasto.",
            reply_markup=ReplyKeyboardMarkup(MENU_PRINCIPAL, one_time_keyboard=True, resize_keyboard=True)
        )

# =============================================================================
# COMANDO DE AYUDA
# =============================================================================

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra información de ayuda"""
    texto_ayuda = (
        "🤖 *Bot de Registro de Gastos - Mucho Derroche*\n\n"
        "*Comandos disponibles:*\n"
        "/nuevo - Registrar un nuevo gasto\n"
        "/cancel - Cancelar el registro actual\n"
        "/ayuda - Mostrar esta ayuda\n\n"
        "*Opciones de registro:*\n"
        "1️⃣ *Manual*: Ingresa los datos paso a paso\n"
        "2️⃣ *Foto*: Sube una foto de la boleta y la procesamos automáticamente\n\n"
        "💡 *Tip*: Al subir una foto, asegúrate de que se vea claramente el monto, fecha y comercio."
    )
    await update.message.reply_text(texto_ayuda, parse_mode='Markdown')

# =============================================================================
# INICIALIZACIÓN Y MAIN
# =============================================================================

def main():
    """Función principal"""
    
    # Crear/actualizar tabla al iniciar
    logger.info("🔄 Inicializando base de datos...")
    create_table()
    
    # Crear aplicación
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # ConversationHandler principal
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
    
    # Registrar handlers
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))
    
    # Iniciar bot
    logger.info("🚀 Bot iniciado correctamente")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
