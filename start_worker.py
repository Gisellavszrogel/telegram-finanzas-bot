#!/usr/bin/env python3
"""
Script para iniciar el worker de RQ que procesa las fotos
"""
import os
import sys
import logging
from redis import Redis
from rq import Worker, Queue, Connection

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

if __name__ == '__main__':
    try:
        redis_conn = Redis.from_url(REDIS_URL)
        redis_conn.ping()
        logger.info(f"✅ Conectado a Redis: {REDIS_URL}")

        with Connection(redis_conn):
            worker = Worker(['fotos'])
            logger.info("🚀 Worker iniciado. Esperando trabajos en cola 'fotos'...")
            worker.work()

    except Exception as e:
        logger.error(f"❌ Error iniciando worker: {e}")
        sys.exit(1)
