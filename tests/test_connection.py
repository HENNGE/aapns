import os
import socket
from asyncio import ensure_future
from ssl import SSLContext

import pytest
from structlog import get_logger

from aapns import connect, Notification, Alert
from aapns.config import Server
from aapns.errors import Disconnected
from tests.fake_apns_server import start_fake_apns_server

pytestmark = pytest.mark.asyncio

CLIENT_CERT_PATH = os.path.join(os.path.dirname(__file__), 'client_cert.pem')
non_verifying_context = SSLContext()


@pytest.fixture(scope='function')
def auto_close(event_loop):
    closeables = []
    try:
        yield lambda x: closeables.append(x) or x
    finally:
        for closeable in closeables:
            event_loop.run_until_complete(closeable.close())


async def test_auto_reconnect(auto_close):
    database = {}
    async with await start_fake_apns_server(database=database) as server:
        config = Server(*server.address)
        port = server.address[1]
        apns = auto_close(await connect(
            CLIENT_CERT_PATH,
            config,
            ssl_context=non_verifying_context,
            auto_reconnect=True,
            timeout=10,
            logger=get_logger()
        ))
        device_id = server.create_device()
        await apns.send_notification(device_id, Notification(Alert('test1')))
        assert apns.connected
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
    async with await start_fake_apns_server(port, database) as server:
        await future
        assert apns.connected
        assert len(server.get_notifications(device_id)) == 2
    await apns.close()


async def test_no_auto_reconnect(auto_close):
    database = {}
    async with await start_fake_apns_server(database=database) as server:
        config = Server(*server.address)
        port = server.address[1]
        apns = auto_close(await connect(
            CLIENT_CERT_PATH,
            config,
            ssl_context=non_verifying_context,
            auto_reconnect=False,
            timeout=10
        ))
        device_id = server.create_device()
        await apns.send_notification(device_id, Notification(Alert('test1')))
        assert apns.connected
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
    async with await start_fake_apns_server(port, database) as server:
        with pytest.raises(Disconnected):
            await future
        assert not apns.connected
        assert len(server.get_notifications(device_id)) == 1