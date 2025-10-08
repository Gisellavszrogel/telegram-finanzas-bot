"""
Sistema de colas con Redis para procesar fotos de boletas
"""
import os
from redis import Redis
from rq import Queue, Retry
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conexión a Redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

try:
    redis_conn = Redis.from_url(REDIS_URL)
    # Test de conexión
    redis_conn.ping()
    logger.info("✅ Conexión exitosa a Redis")
except Exception as e:
    logger.error(f"❌ Error conectando a Redis: {e}")
    redis_conn = None

# Cola principal para procesamiento de fotos
foto_queue = Queue('fotos', connection=redis_conn, default_timeout=300) if redis_conn else None

def encolar_foto(gasto_id, image_base64, chat_id, user_id):
    """
    Encola un trabajo para procesar una foto

    Args:
        gasto_id: ID del registro en PostgreSQL
        image_base64: Imagen en formato base64
        chat_id: ID del chat de Telegram
        user_id: ID del usuario de Telegram

    Returns:
        Job object de RQ o None si falla
    """
    if redis_conn is None or foto_queue is None:
        logger.error("❌ Redis no disponible, no se puede encolar")
        return None

    try:
        job = foto_queue.enqueue(
            'worker.procesar_foto_job',  # Función que ejecutará el worker
            gasto_id,
            image_base64,
            chat_id,
            user_id,
            retry=Retry(max=3, interval=[10, 30, 60]),  # 3 reintentos: 10s, 30s, 60s
            job_timeout=300,  # Timeout de 5 minutos
            failure_ttl=3600  # Guardar info de fallos por 1 hora
        )
        logger.info(f"✅ Job encolado: {job.id} para gasto_id={gasto_id}")
        return job
    except Exception as e:
        logger.error(f"❌ Error encolando job: {e}")
        return None

def get_job_status(job_id):
    """Obtiene el estado de un job"""
    if redis_conn is None:
        return {'status': 'error', 'error': 'Redis no disponible'}
    
    try:
        from rq.job import Job
        job = Job.fetch(job_id, connection=redis_conn)
        
        return {
            'status': job.get_status(),
            'result': job.result if job.is_finished else None,
            'error': job.exc_info if job.is_failed else None
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

def get_queue_info():
    """Retorna estadísticas de la cola"""
    if redis_conn is None or foto_queue is None:
        return {'error': 'Redis no disponible'}
    
    try:
        return {
            'pending': len(foto_queue),
            'started': foto_queue.started_job_registry.count,
            'finished': foto_queue.finished_job_registry.count,
            'failed': foto_queue.failed_job_registry.count
        }
    except Exception as e:
        return {'error': str(e)}
