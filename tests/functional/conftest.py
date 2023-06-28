import asyncio
import shutil
import subprocess
from asyncio import CancelledError, create_subprocess_exec
from contextlib import asynccontextmanager, suppress
from os import killpg
from pathlib import Path
from signal import SIGTERM

import pytest
from aiohttp import web

import aapns.api
import aapns.config
import aapns.models
from aapns.connection import Connection, Request, create_ssl_context
from aapns.pool import Pool

THIS_DIR = Path(__file__).parent


@pytest.fixture(scope="session")
def server_binary(tmp_path_factory):
    if not shutil.which("go"):
        pytest.skip("Go compiler not found")
    workspace = tmp_path_factory.mktemp("server-build")
    server_bin_path = str(workspace / "server")
    subprocess.check_call(
        ["go", "build", "-o", server_bin_path, str(THIS_DIR / "server.go")]
    )
    return server_bin_path


@asynccontextmanager
async def port_receiver():
    fut = asyncio.Future()

    async def port_handler(request: web.Request):
        data = await request.json()
        port = data["port"]
        fut.set_result(port)
        return web.Response(status=204)

    app = web.Application()
    app.add_routes([web.post("/", port_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()
    own_port = site._server.sockets[0].getsockname()[1]
    try:
        yield own_port, fut
    finally:
        await runner.cleanup()


@pytest.fixture()
async def server_port(request, server_binary):
    if not shutil.which("go"):
        pytest.skip("Functional tests use a go server")
    flavour = request.node.get_closest_marker("server_flavor").args[0]
    assert flavour in {"ok", "bad-token", "terminates-connection"}

    async with port_receiver() as (own_port, port_fut):
        server = await create_subprocess_exec(
            server_binary,
            str(own_port),
            flavour,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )
        server_port = await asyncio.wait_for(port_fut, 10)
    yield server_port
    with suppress(ProcessLookupError, PermissionError):
        # server.terminate() is not enough,because `go run`'s child somehow survives
        # Thus, I'm starting the server in a new session and kill the entire session
        server.send_signal(0)  # in case the process is gone
        killpg(server.pid, SIGTERM)
    with suppress(CancelledError):
        await server.wait()


@pytest.fixture
def ssl_context():
    ctx = create_ssl_context()
    ctx.load_verify_locations(cafile="tests/functional/test-server-certificate.pem")
    ctx.load_cert_chain(
        certfile="tests/functional/test-client-certificate.pem",
        keyfile="tests/functional/test-client-certificate.pem",
    )
    return ctx


@pytest.fixture
def request42():
    return Request.new("/3/device/42", {}, {})


@pytest.fixture
async def connection(ssl_context, server_port):
    yield (
        conn := await Connection.create(f"https://localhost:{server_port}", ssl_context)
    )
    await conn.close()


@pytest.fixture
async def pool(ssl_context, server_port):
    yield (
        pool := await Pool.create(f"https://localhost:{server_port}", 2, ssl_context)
    )
    await pool.close()


@pytest.fixture
def target(server_port):
    return aapns.api.Server(
        "tests/functional/test-client-certificate.pem",
        "localhost",
        server_port,
        ca_file="tests/functional/test-server-certificate.pem",
    )


@pytest.fixture
async def client(target):
    client = await target.create_client()
    yield client
    await client.close()


@pytest.fixture
def notification():
    return aapns.models.Notification(
        alert=aapns.models.Alert(title="title", body="body")
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "server_flavor(flavor): type of server to run")