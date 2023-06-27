import asyncio
import itertools
import time

import pytest

import aapns.connection
import aapns.errors

pytestmark = pytest.mark.asyncio


@pytest.mark.server_flavor("ok")
async def test_seqential(pool, request42):
    for i in range(4):
        started = time.time()
        response = await pool.post(request42)
        assert response.code == 200
        assert response.apns_id
        took = time.time() - started
        assert 0.25 < took < 0.3, "Server has ¼s artificial delay"


@pytest.mark.server_flavor("ok")
async def test_parallel(pool, request42):
    started = time.time()
    responses = await asyncio.gather(*(pool.post(request42) for i in range(4)))
    for r in responses:
        assert r.code == 200
        assert r.apns_id
    took = time.time() - started
    assert 0.25 < took < 0.3, "Server has ¼s artificial delay"


@pytest.mark.server_flavor("bad-token")
async def test_bad_token(pool, request42):
    response = await pool.post(request42)
    assert response.code == 400
    assert response.apns_id
    assert response.data["reason"] == "BadDeviceToken"


@pytest.mark.server_flavor("ok")
async def test_closing_pool(server_port, ssl_context, request42):
    c = await aapns.pool.Connection.create(
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
async def test_closed_pool(pool, request42):
    await pool.close()
    with pytest.raises(aapns.errors.Closed):
        await pool.post(request42)


@pytest.mark.server_flavor("terminates-connection")
async def test_termination(pool, request42):
    with pytest.raises(aapns.errors.Timeout):
        # terminating server may break the first request, and definitely breaks the second
        request42.deadline = time.time() + 1
        await pool.post(request42)
        request42.deadline = time.time() + 1
        await pool.post(request42)


@pytest.mark.server_flavor("ok")
async def test_performance(pool, request42):
    """Expected run time is ~3s for 2000 requests, max here is 10."""
    rv = await asyncio.wait_for(
        asyncio.gather(*(pool.post(request42) for i in range(2000))), 10
    )
    for response in rv:
        assert response.code == 200


@pytest.mark.server_flavor("ok")
async def test_resize(pool, request42):
    await pool.post(request42)
    assert len(pool.active) == 2

    pool.resize(4)
    deadline = time.time() + 1
    for i in itertools.count(-3, 0.5):
        await asyncio.sleep(10**i)
        try:
            assert len(pool.active) == 4
            break
        except AssertionError:
            if time.time() > deadline:
                raise
    await asyncio.gather(*(pool.post(request42) for i in "1234"))

    pool.resize(2)
    deadline = time.time() + 1
    for i in itertools.count(-3, 0.5):
        await asyncio.sleep(10**i)
        try:
            assert len(pool.active) == 2
            break
        except AssertionError:
            if time.time() > deadline:
                raise
    await asyncio.gather(*(pool.post(request42) for i in "1234"))


@pytest.mark.server_flavor("ok")
async def test_resize_bad_size(pool):
    with pytest.raises(ValueError):
        pool.resize(0)


@pytest.mark.server_flavor("ok")
async def test_pool_state(pool, request42):
    assert pool.state == "active"
    assert not pool.buffered
    assert not pool.inflight
    assert not pool.pending
    assert not pool.completed
    assert not pool.errors

    await pool.post(request42)
    assert not pool.buffered
    assert not pool.inflight
    assert not pool.pending
    assert pool.completed == 1
    assert not pool.errors
