import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import httpx

from app.core.otto_exceptions import OttoApiException, OttoNotFoundError
from app.infrastructure.rabbitmq_consumer import RabbitMQConsumer
from app.schemas.external_schemes.until_schemes import (
    AvailabilityRequest,
    Controller,
    CreateDeliveryJob,
    DeliveryInformation,
    DeliveryInformationRequest,
    Quantity,
    QuantityRequest,
)


def _sample_job_dict():
    job = CreateDeliveryJob(
        marketplace_job_id="ec956c9d-4141-4c48-8df0-e04f4c20f188",
        availability_data=AvailabilityRequest(
            quantities=QuantityRequest(root=[Quantity(sku="SKU-1", quantity=10)]),
            delivery_information=DeliveryInformationRequest(
                root=[DeliveryInformation(sku="SKU-1", shipping_profile_id="SP-1")]
            ),
        ),
        controller=Controller.JV,
    )
    return job.model_dump_json().encode("utf-8")


def _mock_incoming_message(body: bytes, headers: dict | None = None):
    msg = MagicMock()
    msg.body = body
    msg.headers = headers or {}
    msg.delivery_mode = 2
    msg.redelivered = False
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    msg.nack = AsyncMock()
    return msg


def _mock_otto_exception(status_code: int, endpoint: str = "/v1/availability/quantities"):
    request = httpx.Request("POST", f"https://api.otto.market{endpoint}")
    response = httpx.Response(status_code, request=request, json={"error": f"status {status_code}"})
    if status_code == 404:
        return OttoNotFoundError(
            f"Not found: {status_code}",
            request=request,
            response=response,
            endpoint=endpoint,
            controller="jv",
        )
    return OttoApiException(
        f"OTTO API error {status_code}",
        request=request,
        response=response,
        endpoint=endpoint,
        controller="jv",
    )


@pytest.fixture
def mock_consumer():
    handler = AsyncMock()
    consumer = RabbitMQConsumer(
        url="amqp://guest:guest@localhost:5672/",
        queue_name="test.jobs",
        handler=handler,
    )
    # Mock channel and exchange
    consumer._channel = MagicMock()
    consumer._channel.default_exchange = MagicMock()
    consumer._channel.default_exchange.publish = AsyncMock()
    return consumer


@pytest.mark.asyncio
async def test_process_message_success(mock_consumer):
    msg = _mock_incoming_message(_sample_job_dict())
    mock_consumer._handler.return_value = None

    await mock_consumer._process_message(msg)

    mock_consumer._handler.assert_awaited_once()
    msg.ack.assert_awaited_once()
    msg.reject.assert_not_awaited()
    mock_consumer._channel.default_exchange.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_validation_error(mock_consumer):
    msg = _mock_incoming_message(b'{"invalid": "json structure"}')

    await mock_consumer._process_message(msg)

    mock_consumer._handler.assert_not_awaited()
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    mock_consumer._channel.default_exchange.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_otto_4xx_client_error_rejected(mock_consumer):
    msg = _mock_incoming_message(_sample_job_dict())
    mock_consumer._handler.side_effect = _mock_otto_exception(400)

    await mock_consumer._process_message(msg)

    # 400 Bad Request should immediately reject with requeue=False (no retry)
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    mock_consumer._channel.default_exchange.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_otto_404_rejected(mock_consumer):
    msg = _mock_incoming_message(_sample_job_dict())
    mock_consumer._handler.side_effect = _mock_otto_exception(404)

    await mock_consumer._process_message(msg)

    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    mock_consumer._channel.default_exchange.publish.assert_not_awaited()


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_process_message_retry_republishes_before_ack(mock_sleep, mock_consumer):
    """Verify that during retry, message is FIRST republished with new x-retries, and THEN original is acked."""
    msg = _mock_incoming_message(_sample_job_dict(), headers={})
    mock_consumer._handler.side_effect = _mock_otto_exception(503)

    call_order = []

    async def record_publish(new_message, routing_key):
        call_order.append(("publish", new_message.headers.get("x-retries"), routing_key))

    async def record_ack():
        call_order.append(("ack",))

    mock_consumer._channel.default_exchange.publish.side_effect = record_publish
    msg.ack.side_effect = record_ack

    await mock_consumer._process_message(msg)

    mock_sleep.assert_awaited_once_with(5)  # first retry delay = 5s
    assert len(call_order) == 2
    # Verify order: publish was called BEFORE ack
    assert call_order[0] == ("publish", 1, "test.jobs")
    assert call_order[1] == ("ack",)
    msg.reject.assert_not_awaited()


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_process_message_generic_exception_retry_increments_header(mock_sleep, mock_consumer):
    msg = _mock_incoming_message(_sample_job_dict(), headers={"x-retries": 1})
    mock_consumer._handler.side_effect = RuntimeError("Database temporary error")

    call_order = []
    mock_consumer._channel.default_exchange.publish.side_effect = lambda m, routing_key: call_order.append(
        ("publish", m.headers.get("x-retries"))
    )
    msg.ack.side_effect = lambda: call_order.append(("ack",))

    await mock_consumer._process_message(msg)

    mock_sleep.assert_awaited_once_with(15)  # second retry delay = 15s
    assert call_order == [("publish", 2), ("ack",)]
    msg.reject.assert_not_awaited()


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_process_message_exhausted_retries_rejected(mock_sleep, mock_consumer):
    """When x-retries reaches MAX_RETRIES (3), message must be rejected with requeue=False without republishing."""
    msg = _mock_incoming_message(_sample_job_dict(), headers={"x-retries": 3})
    mock_consumer._handler.side_effect = _mock_otto_exception(500)

    await mock_consumer._process_message(msg)

    mock_sleep.assert_not_awaited()
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
    mock_consumer._channel.default_exchange.publish.assert_not_awaited()
