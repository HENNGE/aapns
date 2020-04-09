""" Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer') 
    * Closed('Server closed the connection')
"""
import asyncio
import logging
import time

import pytest

import aapns.connection
import aapns.errors

pytestmark = pytest.mark.asyncio


async def test_seqential(ok_server, client, notification):
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
    # FIXME
    c = await aapns.api.create_client("https://localhost:2197", ssl_context)
    try:
        task = asyncio.create_task(c.send_notification("42", notification))
        await asyncio.sleep(0.1)
    finally:
        await c.close()

    with pytest.raises(aapns.errors.Closed):
        await task


async def test_closed_client(ok_server, client, notification):
    await client.close()
    with pytest.raises(aapns.errors.Closed):
        await client.send_notification("42", notification)


async def test_termination(terminating_server, client, notification):
    with pytest.raises(aapns.errors.Closed):
        # terminating server may break the first request, and definitely breaks the second
        await client.send_notification("42", notification)
        await client.send_notification("42", notification)
