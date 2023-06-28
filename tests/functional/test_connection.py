import asyncio
import re
import time

import pytest

import aapns.connection
import aapns.errors

pytestmark = pytest.mark.asyncio


@pytest.mark.server_flavor("ok")
async def test_seqential(connection, request42):
    for i in range(4):
        started = time.time()
        response = await connection.post(request42)
        assert response.code == 200
        assert response.apns_id
        took = time.time() - started
        assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


@pytest.mark.server_flavor("ok")
async def test_parallel(connection, request42):
    started = time.time()
    responses = await asyncio.gather(*(connection.post(request42) for i in range(4)))
    for r in responses:
        assert r.code == 200
        assert r.apns_id
    took = time.time() - started
    assert 0.25 < took < 0.3, "Server has 1/4s artificial delay"


@pytest.mark.server_flavor("bad-token")
async def test_bad_token(connection, request42):
    response = await connection.post(request42)
    assert response.code == 400
    assert response.apns_id
    assert response.data["reason"] == "BadDeviceToken"


@pytest.mark.server_flavor("ok")
async def test_closing_connection(server_port, ssl_context, request42):
    c = await aapns.connection.Connection.create(
        f"https://localhost:{server_port}", ssl_context
    )
    try:
        task = asyncio.create_task(c.post(request42))
        await asyncio.sleep(0.1)
    finally:
        await c.close()

    with pytest.raises(aapns.errors.Closed):
        await task


@pytest.mark.server_flavor("ok")
async def test_closed_connection(connection, request42):
    await connection.close()
    with pytest.raises(aapns.errors.Closed):
        await connection.post(request42)


@pytest.mark.server_flavor("terminates-connection")
async def test_termination(connection, request42):
    """Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer')
    * Closed('Server closed the connection')
    * Closed('0') means ErrorCodes.NO_ERROR
    """
    match = re.compile(
        "ErrorCodes.NO_ERROR|Connection reset by peer|Server closed the connection|0"
    )
    with pytest.raises(aapns.errors.Closed, match=match):
        # terminating server may break the first request, and definitely breaks the second
        await connection.post(request42)
        await connection.post(request42)


@pytest.mark.server_flavor("ok")
async def test_connection_state(connection):
    assert connection.state == "active"