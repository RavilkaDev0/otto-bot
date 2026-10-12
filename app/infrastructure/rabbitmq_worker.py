import asyncio
import logging

from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.infrastructure.rabbitmq_consumer import RabbitMQConsumer
from app.schemas.external_schemes.until_schemes import CreateDeliveryJob
from app.services.extermal_service import ExternalService
from app.core.otto_auth import OttoAuth
from app.core.logger import setup_logging


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("external_api")
    
    logger.info("RabbitMQ worker запускается")
    
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret
    )
    
    client = ExternalOttoClient(
        auth=auth,
        base_url=settings.otto_base_url
    )
    
    service = ExternalService(client=client)
    
    rabbitmq_url = (
        f"amqp://{settings.rabbitmq_app_user}:"
        f"{settings.rabbitmq_app_password}@"
        f"{settings.rabbitmq_host}:"
        f"{settings.rabbitmq_port}/"
        f"{settings.rabbitmq_virtual_host}"
    )
    
    logger.info("Консюмер запускается...")
    
    consumer = RabbitMQConsumer(
        url=rabbitmq_url,
        handler=service.create_or_update_availability,
        queue_name="otto.jobs"
    )
    
    
    try:
        await consumer.connect()
        
        await asyncio.Future()
        logger.info("Консюмер подключился")
    finally:
        await consumer.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())