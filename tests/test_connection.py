import asyncio
import os
from asyncio import ensure_future
from ssl import SSLContext
from tempfile import TemporaryDirectory

import pytest
from structlog import get_logger

from aapns import connect, Notification, Alert
from aapns.config import Server
from aapns.errors import Disconnected, BadDeviceToken
from tests.fake_apns_server import start_fake_apns_server
from tests.fake_client_cert import create_client_cert

pytestmark = pytest.mark.asyncio

non_verifying_context = SSLContext()


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
        path = os.path.join(workspace, 'cert.pem')
        with open(path, 'wb') as fobj:
            fobj.write(create_client_cert())
        yield path


@pytest.fixture
async def client(client_cert_path):
    async with start_fake_apns_server() as server:
        apns = await connect(
            client_cert_path,
            Server(*server.address),
            ssl_context=non_verifying_context,
            auto_reconnect=True,
            timeout=10,
            logger=get_logger()
        )
        try:
            yield apns
        finally:
            await apns.close()


async def test_auto_reconnect(auto_close, client_cert_path):
    database = {}
    async with start_fake_apns_server(database=database) as server:
        config = Server(*server.address)
        port = server.address[1]
        apns = auto_close(await connect(
            client_cert_path,
            config,
            ssl_context=non_verifying_context,
            auto_reconnect=True,
            timeout=10,
            logger=get_logger()
        ))
        device_id = server.create_device()
        await apns.send_notification(device_id, Notification(Alert('test1')))
        assert len(server.get_notifications(device_id)) == 1

    with pytest.raises(Disconnected):
        await apns.send_notification(
            device_id,
            Notification(Alert('test2'))
        )

    future = ensure_future(apns.send_notification(
        device_id,
        Notification(Alert('test3'))
    ))
    async with start_fake_apns_server(port, database) as server:
        await future
        assert len(server.get_notifications(device_id)) == 2
    await apns.close()


async def test_no_auto_reconnect(auto_close, client_cert_path):
    database = {}
    async with start_fake_apns_server(database=database) as server:
        config = Server(*server.address)
        port = server.address[1]
        apns = auto_close(await connect(
            client_cert_path,
            config,
            ssl_context=non_verifying_context,
            auto_reconnect=False,
            timeout=10
        ))
        device_id = server.create_device()
        await apns.send_notification(device_id, Notification(Alert('test1')))
        assert len(server.get_notifications(device_id)) == 1

    with pytest.raises(Disconnected):
        await apns.send_notification(
            device_id,
            Notification(Alert('test2'))
        )

    future = ensure_future(apns.send_notification(
        device_id,
        Notification(Alert('test3')))
    )
    async with start_fake_apns_server(port, database) as server:
        with pytest.raises(Disconnected):
            await future
        assert len(server.get_notifications(device_id)) == 1


async def test_slow_server(auto_close, client_cert_path):
    database = {}
    async with start_fake_apns_server(database=database, lag=0.5) as server:
        config = Server(*server.address)
        apns = auto_close(await connect(
            client_cert_path,
            config,
            ssl_context=non_verifying_context,
            auto_reconnect=False,
            timeout=0.1
        ))
        device_id = server.create_device()
        with pytest.raises(asyncio.TimeoutError):
            await apns.send_notification(device_id, Notification(Alert('test1')))


async def test_bad_device_id(client):
    with pytest.raises(BadDeviceToken):
        await client.send_notification('does not exist', Notification(Alert('test')))
