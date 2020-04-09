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


async def test_seqential(ok_server, connection, request42):
    for i in range(4):
        started = time.time()
        response = await connection.post(request42)
        assert response.code == 200
        assert response.apns_id
        took = time.time() - started
        assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


async def test_parallel(ok_server, connection, request42):
    started = time.time()
    responses = await asyncio.gather(*(connection.post(request42) for i in range(4)))
    for r in responses:
        assert r.code == 200
        assert r.apns_id
    took = time.time() - started
    assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


async def test_bad_token(bad_token_server, connection, request42):
    response = await connection.post(request42)
    assert response.code == 400
    assert response.apns_id
    assert response.data["reason"] == "BadDeviceToken"


async def test_closing_connection(ok_server, ssl_context, request42):
    c = await aapns.connection.Connection.create("https://localhost:2197", ssl_context)
    try:
        task = asyncio.create_task(c.post(request42))
        await asyncio.sleep(0.1)
    finally:
        await c.close()

    with pytest.raises(aapns.errors.Closed):
        await task


async def test_closed_connection(ok_server, connection, request42):
    await connection.close()
    with pytest.raises(aapns.errors.Closed):
        await connection.post(request42)


async def test_termination(terminating_server, connection, request42):
    with pytest.raises(aapns.errors.Closed):
        # terminating server may break the first request, and definitely breaks the second
        await connection.post(request42)
        await connection.post(request42)
