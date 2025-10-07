"""
Worker que procesa fotos de boletas en background
Ejecutar con: rq worker fotos --url $REDIS_URL
"""
import os
import logging
import requests
import psycopg2
from datetime import datetime
import json

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_PUBLIC_URL')
N8N_ENDPOINT = os.getenv('N8N_ENDPOINT')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

def procesar_foto_job(gasto_id, image_path, chat_id, user_id):
    """
    Función principal que ejecuta el worker
    
    Steps:
    1. Enviar imagen a n8n
    2. Esperar respuesta con datos extraídos
    3. Actualizar PostgreSQL
    4. Notificar al usuario en Telegram
    """
    logger.info(f"🔄 Iniciando procesamiento: gasto_id={gasto_id}, image={image_path}")
    
    try:
        # PASO 1: Enviar imagen a n8n
        logger.info(f"📤 Enviando imagen a n8n...")
        ocr_data = enviar_a_n8n(image_path)
        
        if not ocr_data:
            raise Exception("n8n no devolvió datos válidos")
        
        logger.info(f"✅ Datos recibidos de n8n: {ocr_data}")
        
        # PASO 2: Actualizar PostgreSQL
        actualizar_bd(gasto_id, ocr_data, status='processed')
        
        # PASO 3: Notificar al usuario
        enviar_confirmacion_telegram(chat_id, gasto_id, ocr_data)
        
        logger.info(f"✅ Procesamiento completado para gasto_id={gasto_id}")
        return {'success': True, 'gasto_id': gasto_id, 'data': ocr_data}
        
    except Exception as e:
        logger.error(f"❌ Error procesando gasto_id={gasto_id}: {e}")
        
        # Marcar como error en BD
        try:
            actualizar_bd(gasto_id, {'error': str(e)}, status='error')
        except:
            pass
        
        # Notificar error al usuario
        enviar_error_telegram(chat_id, gasto_id)
        
        raise  # Re-raise para que RQ lo marque como fallido

def enviar_a_n8n(image_path):
    """
    Envía la imagen a n8n y retorna los datos extraídos
    """
    logger.info(f"📤 Enviando {image_path} a n8n: {N8N_ENDPOINT}")
    
    if not N8N_ENDPOINT:
        logger.error("❌ N8N_ENDPOINT no configurado en variables de entorno")
        return None
    
    try:
        with open(image_path, 'rb') as f:
            files = {'file': (os.path.basename(image_path), f, 'image/jpeg')}
            
            response = requests.post(
                N8N_ENDPOINT,
                files=files,
                timeout=60  # Timeout de 1 minuto
            )
        
        response.raise_for_status()
        data = response.json()
        
        logger.info(f"✅ Respuesta de n8n recibida: {data}")
        return data
        
    except requests.Timeout:
        logger.error("⏱️ Timeout esperando respuesta de n8n (>60s)")
        return None
    except requests.RequestException as e:
        logger.error(f"🌐 Error de red con n8n: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"📄 Respuesta de n8n no es JSON válido: {response.text[:200]}")
        return None
    except FileNotFoundError:
        logger.error(f"📁 Archivo no encontrado: {image_path}")
        return None

def actualizar_bd(gasto_id, ocr_data, status):
    """
    Actualiza el registro en PostgreSQL con los datos extraídos
    """
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()
        
        # Extraer datos del OCR (con valores por defecto si no existen)
        fecha_str = ocr_data.get('fecha')
        monto = ocr_data.get('monto')
        categoria = ocr_data.get('categoria')
        descripcion = ocr_data.get('descripcion')
        tipo_gasto = ocr_data.get('tipo_gasto')
        banco = ocr_data.get('banco')
        
        # Convertir fecha si existe
        fecha_obj = None
        if fecha_str:
            try:
                # Intentar varios formatos de fecha
                for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']:
                    try:
                        fecha_obj = datetime.strptime(fecha_str, fmt).date()
                        break
                    except:
                        continue
            except:
                logger.warning(f"⚠️ No se pudo parsear la fecha: {fecha_str}")
        
        cursor.execute("""
            UPDATE finanzas 
            SET 
                status = %s,
                ocr_data = %s,
                processed_at = %s,
                fecha = COALESCE(%s, fecha),
                monto = COALESCE(%s, monto),
                categoria = COALESCE(%s, categoria),
                descripcion = COALESCE(%s, descripcion),
                tipo_gasto = COALESCE(%s, tipo_gasto),
                banco = COALESCE(%s, banco)
            WHERE id = %s
        """, (
            status,
            json.dumps(ocr_data),
            datetime.now(),
            fecha_obj,
            float(monto) if monto else None,
            categoria,
            descripcion,
            tipo_gasto,
            banco,
            gasto_id
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"💾 BD actualizada para gasto_id={gasto_id}, status={status}")
        
    except Exception as e:
        logger.error(f"❌ Error actualizando BD para gasto_id={gasto_id}: {e}")
        raise

def enviar_confirmacion_telegram(chat_id, gasto_id, ocr_data):
    """
    Envía mensaje de confirmación al usuario con los datos extraídos
    """
    try:
        # Formatear monto
        monto = ocr_data.get('monto', 'No detectado')
        if isinstance(monto, (int, float)):
            monto = f"${monto:,.0f}".replace(',', '.')
        
        # Formatear mensaje
        mensaje = f"""📋 *Datos extraídos de tu boleta:*

💰 *Monto:* {monto}
📅 *Fecha:* {ocr_data.get('fecha', 'No detectada')}
🏷️ *Categoría:* {ocr_data.get('categoria', 'No detectada')}
🏪 *Comercio:* {ocr_data.get('descripcion', 'No detectado')}

¿Los datos son correctos?
"""
        
        # Botones inline
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Guardar", "callback_data": f"confirm_{gasto_id}"},
                    {"text": "✏️ Editar", "callback_data": f"edit_{gasto_id}"}
                ],
                [
                    {"text": "🗑️ Cancelar", "callback_data": f"cancel_{gasto_id}"}
                ]
            ]
        }
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': mensaje,
            'parse_mode': 'Markdown',
            'reply_markup': json.dumps(keyboard)
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        
        logger.info(f"✅ Mensaje de confirmación enviado a chat_id={chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error enviando mensaje a Telegram: {e}")
        raise

def enviar_error_telegram(chat_id, gasto_id):
    """
    Notifica al usuario que hubo un error procesando su boleta
    """
    try:
        mensaje = f"""❌ *Error procesando tu boleta*

Lo siento, no pude extraer los datos automáticamente.

Posibles causas:
- La imagen no es clara
- El formato de la boleta no es reconocible
- Error de conexión con el servicio

¿Qué quieres hacer?
"""
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🖋 Ingresar manualmente", "callback_data": f"manual_{gasto_id}"},
                    {"text": "🔄 Intentar de nuevo", "callback_data": f"retry_{gasto_id}"}
                ],
                [
                    {"text": "🗑️ Cancelar", "callback_data": f"cancel_{gasto_id}"}
                ]
            ]
        }
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': mensaje,
            'parse_mode': 'Markdown',
            'reply_markup': json.dumps(keyboard)
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        
        logger.info(f"📨 Mensaje de error enviado a chat_id={chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error enviando mensaje de error: {e}")

# Para testing local
if __name__ == "__main__":
    print("⚠️ Este archivo debe ejecutarse con RQ Worker")
    print("Comando: rq worker fotos --url $REDIS_URL")
    print("\nVariables de entorno necesarias:")
    print("  - REDIS_URL")
    print("  - DATABASE_PUBLIC_URL")
    print("  - N8N_ENDPOINT")
    print("  - TELEGRAM_TOKEN")
