import logging
import asyncio
import aio_pika
from collections.abc import Awaitable, Callable
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection
from pydantic import ValidationError

from app.core.otto_exceptions import OttoApiException
from app.schemas.external_schemes.until_schemes import CreateDeliveryJob

logger = logging.getLogger("external_api")

JobHandler = Callable[[CreateDeliveryJob], Awaitable[None]]


class RabbitMQConsumer:
    MAX_RETRIES: int = 3
    RETRY_DELAYS: list[int] = [5, 15, 30]

    def __init__(
        self,
        url: str,
        queue_name: str,
        handler: JobHandler,
    ):
        self._url = url
        self._queue_name = queue_name
        self._handler = handler

        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)

        channel = self._connection.channel()
        await channel.initialize()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(self._queue_name, durable=True)
        self._channel = channel

        await queue.consume(self._process_message)

        logger.info(
            "RabbitMQ consumer слушает очередь: %s",
            self._queue_name,
        )

    async def _handle_retry(
        self,
        message: AbstractIncomingMessage,
        job: CreateDeliveryJob,
        retries: int,
    ) -> None:
        if retries >= self.MAX_RETRIES:
            logger.error(
                "Превышено максимальное количество попыток (%d) для job: task_id=%s. Удаляем из очереди.",
                self.MAX_RETRIES,
                job.task_id,
            )
            await message.reject(requeue=False)
            return

        delay = self.RETRY_DELAYS[min(retries, len(self.RETRY_DELAYS) - 1)]
        logger.warning(
            "Повторная попытка обработки job через %ds (попытка %d/%d): task_id=%s",
            delay,
            retries + 1,
            self.MAX_RETRIES,
            job.task_id,
        )
        await asyncio.sleep(delay)

        new_headers = dict(message.headers or {})
        new_headers["x-retries"] = retries + 1

        new_message = aio_pika.Message(
            body=message.body,
            headers=new_headers,
            delivery_mode=message.delivery_mode,
        )

        # Сначала переопубликовываем с новым x-retries, чтобы не получить потерю сообщения
        if self._channel is not None:
            await self._channel.default_exchange.publish(
                new_message,
                routing_key=self._queue_name,
            )
        # Исходное сообщение подтверждаем только после успешной публикации
        await message.ack()

    async def _process_message(
        self,
        message: AbstractIncomingMessage,
    ) -> None:
        raw_payload = message.body.decode(
            "utf-8",
            errors="replace",
        )

        logger.info(
            "Получено RabbitMQ сообщение: redelivered=%s payload=%s",
            message.redelivered,
            raw_payload,
        )

        try:
            job = CreateDeliveryJob.model_validate_json(message.body)
        except ValidationError:
            logger.exception(
                "ПОлучено некорректное сообщение: %r",
                message.body,
            )
            await message.reject(requeue=False)
            return

        logger.info(
            "Расрапсенная job:\n%s",
            job.model_dump_json(indent=2),
        )

        logger.info(
            "Получена задача: task_id=%s marketplace_job_id=%s",
            job.task_id,
            job.marketplace_job_id,
        )

        headers = message.headers or {}
        retries = int(headers.get("x-retries") or 0)

        try:
            await self._handler(job)
        except OttoApiException as exc:
            logger.exception(
                "OttoApiException при обработке job: task_id=%s status=%s endpoint=%s",
                job.task_id,
                exc.status_code,
                exc.endpoint,
            )
            if 400 <= exc.status_code < 500:
                logger.error(
                    "Критичная ошибка клиента (4xx): status=%s endpoint=%s job_id=%s. Удаляем из очереди без ретрая.",
                    exc.status_code,
                    exc.endpoint,
                    job.task_id,
                )
                await message.reject(requeue=False)
                return
            await self._handle_retry(message, job, retries)
            return
        except Exception:
            logger.exception(
                "Ошибка обработки job: task_id=%s",
                job.task_id,
            )
            await self._handle_retry(message, job, retries)
            return

        await message.ack()

        logger.info(
            "Залача успешно обработана: task_id=%s",
            job.task_id,
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()

