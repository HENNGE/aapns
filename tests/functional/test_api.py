import asyncio
import logging
import time

import pytest

import aapns.connection
import aapns.errors
from aapns.api import Server

pytestmark = pytest.mark.asyncio


async def test_sequential(ok_server, client, notification):
    for i in range(4):
        started = time.time()
        apns_id = await client.send_notification("42", notification)
        assert apns_id
        took = time.time() - started
        assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


async def test_parallel(ok_server, client, notification):
    started = time.time()
    apns_ids = await asyncio.gather(
        *(client.send_notification("42", notification) for i in range(4))
    )
    for apns_id in apns_ids:
        assert apns_id
    took = time.time() - started
    assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


async def test_bad_token(bad_token_server, client, notification):
    with pytest.raises(aapns.errors.BadDeviceToken):
        await client.send_notification("42", notification)


async def test_closing_client(ok_server, ssl_context, notification):
    client = await Server(
        "tests/functional/test-client-certificate.pem",
        "localhost",
        2197,
        ca_file="tests/functional/test-server-certificate.pem",
    ).create_client()
    try:
        task = asyncio.create_task(client.send_notification("42", notification))
        await asyncio.sleep(0)
    finally:
        await client.close()

    with pytest.raises(aapns.errors.Closed):
        await task


async def test_closed_client(ok_server, client, notification):
    await client.close()
    with pytest.raises(aapns.errors.Closed):
        await client.send_notification("42", notification)


async def test_termination(terminating_server, client, notification):
    with pytest.raises(aapns.errors.Timeout):
        # terminating server may break the first request, and definitely breaks the second
        await client.send_notification("42", notification)
        await client.send_notification("42", notification)
