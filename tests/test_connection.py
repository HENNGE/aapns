import errno
import socket
import ssl
from unittest.mock import AsyncMock, Mock

import h2.config
import h2.connection
import pytest

from aapns.connection import TCP_USER_TIMEOUT, Connection

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "origin",
    (
        "just.hostname.is.invalid",
        "http://localhost",
        "https://user@localhost:1234",
        "https://user:pass@localhost:1234",
        "https://localhost:1234/foo/bar",
        "https://localhost:1234?q=ax",
        "https://localhost:1234;p=ax",
        "https://localhost:1234#frag",
    ),
)
async def test_bad_origin(origin):
    with pytest.raises(ValueError):
        await Connection.create(origin)


async def test_bad_context():
    context = ssl.SSLContext()
    context.options = 0
    with pytest.raises(ValueError):
        await Connection.create("https://localhost:1234", context)


async def test_dead_peer_detection(monkeypatch):
    sock = socket.socket()
    ssl_object = Mock(**{"selected_alpn_protocol.return_value": "h2"})
    extra_info = {"socket": sock, "ssl_object": ssl_object}
    writer = Mock(get_extra_info=extra_info.get)

    async def open_connection(*args, **kwargs):
        return Mock(), writer

    monkeypatch.setattr("aapns.connection.open_connection", open_connection)
    monkeypatch.setattr("aapns.connection.Connection.__post_init__", Mock())
    with sock:
        await Connection.create("https://localhost:1234")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
        if hasattr(socket, "TCP_USER_TIMEOUT"):
            timeout = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT)
            assert timeout == TCP_USER_TIMEOUT * 1000


@pytest.mark.parametrize(
    "error",
    (
        TimeoutError(errno.ETIMEDOUT, "Operation timed out"),
        OSError(errno.EHOSTUNREACH, "No route to host"),
    ),
)
async def test_network_error(caplog, error):
    conn = connection_failing_with(error)
    await conn.reader
    assert conn.closed
    assert conn.outcome == str(error)
    assert "background read task died" not in caplog.text
    await conn.close()


async def test_tls_error(caplog):
    # e.g. client certificate rejected by the server after TLS 1.3 handshake
    error = ssl.SSLError("tlsv1 alert certificate expired")
    conn = connection_failing_with(error)
    await conn.reader
    assert conn.closed
    assert conn.outcome == str(error)
    assert "TLS error" in caplog.text
    await conn.close()


def connection_failing_with(error):
    protocol = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True))
    reader = Mock(read=AsyncMock(side_effect=error))
    writer = Mock(wait_closed=AsyncMock())
    return Connection("localhost", 1234, protocol, reader, writer)
