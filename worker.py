"""
Worker que procesa fotos de boletas desde base64
"""
import os
import logging
import requests
import psycopg2
from datetime import datetime
import json
import base64
import io

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_PUBLIC_URL')
N8N_ENDPOINT = os.getenv('N8N_ENDPOINT')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

def procesar_foto_job(gasto_id, image_base64, chat_id, user_id):
    """
    Procesa foto desde base64
    """
    logger.info(f"🔄 Procesando gasto_id={gasto_id}")
    
    try:
        logger.info(f"📤 Enviando imagen a n8n...")
        ocr_data = enviar_a_n8n(image_base64)
        
        if not ocr_data:
            raise Exception("n8n no devolvió datos válidos")
        
        logger.info(f"✅ Datos recibidos de n8n: {ocr_data}")
        
        actualizar_bd(gasto_id, ocr_data, status='processed')
        enviar_confirmacion_telegram(chat_id, gasto_id, ocr_data)
        
        logger.info(f"✅ Completado gasto_id={gasto_id}")
        return {'success': True, 'gasto_id': gasto_id, 'data': ocr_data}
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        
        try:
            actualizar_bd(gasto_id, {'error': str(e)}, status='error')
        except:
            pass
        
        enviar_error_telegram(chat_id, gasto_id)
        raise

def enviar_a_n8n(image_base64):
    """
    Envía imagen en base64 a n8n
    """
    if not N8N_ENDPOINT:
        logger.error("❌ N8N_ENDPOINT no configurado")
        return None
    
    try:
        logger.info(f"📤 Preparando imagen para n8n...")
        
        # Decodificar base64
        image_bytes = base64.b64decode(image_base64)
        logger.info(f"✅ Imagen decodificada, tamaño: {len(image_bytes)} bytes")
        
        # Crear archivo en memoria
        image_file = io.BytesIO(image_bytes)
        image_file.seek(0)
        
        # Enviar como multipart/form-data
        files = {'file': ('boleta.jpg', image_file, 'image/jpeg')}
        
        logger.info(f"🌐 Enviando POST a: {N8N_ENDPOINT}")
        response = requests.post(N8N_ENDPOINT, files=files, timeout=60)
        
        logger.info(f"📥 Status code: {response.status_code}")
        logger.info(f"📥 Response preview: {response.text[:300]}")
        
        response.raise_for_status()
        data = response.json()
        
        logger.info(f"✅ JSON recibido correctamente")
        return data
        
    except base64.binascii.Error as e:
        logger.error(f"❌ Error decodificando base64: {e}")
        return None
    except requests.Timeout:
        logger.error("⏱️ Timeout esperando respuesta de n8n (>60s)")
        return None
    except requests.RequestException as e:
        logger.error(f"🌐 Error de conexión con n8n: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"📄 n8n no devolvió JSON válido")
        logger.error(f"Respuesta recibida: {response.text[:500]}")
        return None
    except Exception as e:
        logger.error(f"❌ Error inesperado: {type(e).__name__}: {e}")
        return None

def actualizar_bd(gasto_id, ocr_data, status):
    """
    Actualiza BD con datos del OCR
    """
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()
        
        fecha_str = ocr_data.get('fecha')
        monto = ocr_data.get('monto')
        categoria = ocr_data.get('categoria')
        descripcion = ocr_data.get('descripcion')
        tipo_gasto = ocr_data.get('tipo_gasto')
        banco = ocr_data.get('banco')
        
        fecha_obj = None
        if fecha_str:
            try:
                for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y']:
                    try:
                        fecha_obj = datetime.strptime(fecha_str, fmt).date()
                        break
                    except:
                        continue
            except:
                logger.warning(f"⚠️ Fecha inválida: {fecha_str}")
        
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
        
        logger.info(f"💾 BD actualizada: gasto_id={gasto_id}, status={status}")
        
    except Exception as e:
        logger.error(f"❌ Error BD: {e}")
        raise

def enviar_confirmacion_telegram(chat_id, gasto_id, ocr_data):
    """
    Envía confirmación con botones
    """
    try:
        monto = ocr_data.get('monto', 'No detectado')
        if isinstance(monto, (int, float)):
            monto = f"${monto:,.0f}".replace(',', '.')
        
        mensaje = f"""📋 *Datos extraídos:*

💰 Monto: {monto}
📅 Fecha: {ocr_data.get('fecha', 'No detectada')}
🏷️ Categoría: {ocr_data.get('categoria', 'No detectada')}
🏪 Comercio: {ocr_data.get('descripcion', 'No detectado')}

¿Son correctos?"""
        
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
        
        logger.info(f"✅ Confirmación enviada a chat_id={chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error enviando mensaje: {e}")
        raise

def enviar_error_telegram(chat_id, gasto_id):
    """
    Notifica error al usuario
    """
    try:
        mensaje = """❌ *Error procesando boleta*

No pude extraer los datos.

¿Qué hacer?"""
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🖋 Ingresar manual", "callback_data": f"manual_{gasto_id}"},
                    {"text": "🔄 Reintentar", "callback_data": f"retry_{gasto_id}"}
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
        
        logger.info(f"📨 Error enviado a chat_id={chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error enviando error: {e}")

if __name__ == "__main__":
    print("⚠️ Ejecutar con: rq worker fotos --url $REDIS_URL")
    if __name__ == "__main__":
        
    import base64

    # 🔹 Ruta local de una imagen de prueba
    imagen_prueba = "boleta.jpg"

    # 🔹 Reemplaza por un gasto_id cualquiera (no importa si no existe)
    gasto_id = 999
    chat_id = 123456789  # tu chat_id real de Telegram
    user_id = 123456789  # tu user_id real de Telegram

    try:
        print("🧪 Iniciando prueba de procesamiento local...\n")
        with open(imagen_prueba, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        result = procesar_foto_job(gasto_id, b64, chat_id, user_id)
        print("\n✅ Resultado de prueba:\n", result)

    except Exception as e:
        import traceback
        print("\n❌ Error durante la prueba:")
        traceback.print_exc()


