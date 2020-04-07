import asyncio
import os
from asyncio import ensure_future
from ssl import SSLContext
from tempfile import TemporaryDirectory

import pytest
from aapns.api import create_client
from aapns.config import Server
from aapns.errors import BadDeviceToken, Disconnected
from aapns.models import Alert, Notification
from httpx import AsyncClient
from tests.fake_apns_server import start_fake_apns_server
from tests.fake_client_cert import create_client_cert

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def auto_close(event_loop):
    closeables = []
    try:
        yield lambda x: closeables.append(x) or x
    finally:
        for closeable in closeables:
            await closeable.close()


@pytest.fixture
def client_cert_path():
    with TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "cert.pem")
        with open(path, "wb") as fobj:
            fobj.write(create_client_cert())
        yield path


@pytest.fixture
async def client(client_cert_path, monkeypatch):
    original = AsyncClient.__init__

    def non_verify_init(*args, **kwargs):
        return original(*args, **kwargs, verify=False)

    monkeypatch.setattr(AsyncClient, "__init__", non_verify_init)
    async with start_fake_apns_server() as server:
        apns = await create_client(client_cert_path, Server(*server.address))
        try:
            yield apns
        finally:
            await apns.close()


async def test_bad_device_id(client):
    with pytest.raises(BadDeviceToken):
        await client.send_notification("does not exist", Notification(Alert("test")))
