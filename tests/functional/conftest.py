import logging
from asyncio import CancelledError, create_subprocess_exec, create_task, gather, sleep
from asyncio.subprocess import PIPE
from contextlib import asynccontextmanager, suppress
from os import killpg
from signal import SIGTERM

import pytest

import aapns.api
import aapns.config
import aapns.models
from aapns.connection import Connection, Request, create_ssl_context
from aapns.pool import Pool


async def collect(stream, name, output=[]):
    with suppress(CancelledError):
        async for blob in stream:
            line = blob.decode("utf-8").strip()
            logging.warning("%s: %s", name, line)
            output.append(line)


@asynccontextmanager
async def server_factory(flavour):
    server = await create_subprocess_exec(
        "go",
        "run",
        f"tests/functional/server-{flavour}.go",
        stdout=PIPE,
        stderr=PIPE,
        start_new_session=True,
    )
    try:
        output = []
        collectors = (
            create_task(collect(server.stdout, "server:stdout", output)),
            create_task(collect(server.stderr, "server:stderr", output)),
        )
        try:
            for delay in (2 ** i for i in range(-10, 3)):  # max 8s total
                await sleep(delay)
                if "exit status" in " ".join(output):
                    raise OSError(f"test server {flavour!r} crashed")
                if "Serving on" in " ".join(output):
                    break
            else:
                raise TimeoutError(f"test server {flavour!r} did not come up")
            yield server
        finally:
            for c in collectors:
                c.cancel()
            with suppress(CancelledError):
                await gather(*collectors)
    finally:
        with suppress(ProcessLookupError, PermissionError):
            # server.terminate() is not enough,because `go run`'s child somehow survives
            # Thus, I'm starting the server in a new session and kill the entire session
            server.send_signal(0)  # in case the process is gone
            killpg(server.pid, SIGTERM)
        with suppress(CancelledError):
            await server.wait()


@pytest.fixture
async def ok_server():
    async with server_factory("ok") as s:
        yield s


@pytest.fixture
async def bad_token_server():
    async with server_factory("bad-token") as s:
        yield s


@pytest.fixture
async def terminating_server():
    async with server_factory("terminates-connection") as s:
        yield s


@pytest.fixture
def ssl_context():
    ctx = create_ssl_context()
    ctx.load_verify_locations(cafile=".test-server-certificate.pem")
    ctx.load_cert_chain(
        certfile=".test-client-certificate.pem", keyfile=".test-client-certificate.pem"
    )
    return ctx


@pytest.fixture
def request42():
    return Request.new("/3/device/42", {}, {})


@pytest.fixture
async def connection(ssl_context):
    yield (conn := await Connection.create("https://localhost:2197", ssl_context))
    await conn.close()


@pytest.fixture
async def pool(ssl_context):
    yield (pool := await Pool.create("https://localhost:2197", 2, ssl_context))
    await pool.close()


@pytest.fixture
async def client():
    client = await aapns.api.create_client(
        ".test-client-certificate.pem",
        aapns.config.Server("localhost", 2197),
        cafile=".test-server-certificate.pem",
    )
    yield client
    await client.close()


@pytest.fixture
def notification():
    return aapns.models.Notification(
        alert=aapns.models.Alert(title="title", body="body")
    )
