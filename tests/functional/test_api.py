import asyncio
import time

import pytest

import aapns.connection
import aapns.errors
from aapns.api import Server


@pytest.mark.server_flavor("ok")
async def test_seqential(client, notification):
    for i in range(4):
        started = time.time()
        apns_id = await client.send_notification("42", notification)
        assert apns_id
        took = time.time() - started
        assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


@pytest.mark.server_flavor("ok")
async def test_parallel(client, notification):
    started = time.time()
    apns_ids = await asyncio.gather(
        *(client.send_notification("42", notification) for i in range(4))
    )
    for apns_id in apns_ids:
        assert apns_id
    took = time.time() - started
    assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


@pytest.mark.server_flavor("bad-token")
async def test_bad_token(client, notification):
    with pytest.raises(aapns.errors.BadDeviceToken):
        await client.send_notification("42", notification)


@pytest.mark.server_flavor("ok")
async def test_closing_client(server_port, ssl_context, notification):
    client = await Server(
        "tests/functional/test-client-certificate.pem",
        "localhost",
        server_port,
        ca_file="tests/functional/test-server-certificate.pem",
    ).create_client()
    try:
        task = asyncio.create_task(client.send_notification("42", notification))
        await asyncio.sleep(0)
    finally:
        await client.close()

    with pytest.raises(aapns.errors.Closed):
        await task


@pytest.mark.server_flavor("ok")
async def test_closed_client(client, notification):
    await client.close()
    with pytest.raises(aapns.errors.Closed):
        await client.send_notification("42", notification)


@pytest.mark.server_flavor("terminates-connection")
async def test_termination(client, notification):
    with pytest.raises(aapns.errors.Timeout):
        # terminating server may break the first request, and definitely breaks the second
        await client.send_notification("42", notification)
        await client.send_notification("42", notification)