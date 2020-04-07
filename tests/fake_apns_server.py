import asyncio
import json
import os
import secrets
import ssl
import tempfile
import uuid
from asyncio import Protocol
from contextlib import asynccontextmanager
from functools import wraps

import attr
from aapns.errors import BadDeviceToken
from cryptography.hazmat.primitives import serialization
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded
from tests.fake_client_cert import gen_certificate, gen_private_key


@attr.s
class Response:
    headers = attr.ib()
    body = attr.ib(default=None)


@attr.s
class Request:
    headers = attr.ib()
    body = attr.ib(default=b"", repr=False)

    def handle(self, server):
        headers = dict(self.headers)
        if headers[b":method"] != b"POST":
            return Response([(b":status", b"405"), (b"content-length", b"0")])
        apns_id = headers.get(b"apns-id", str(uuid.uuid4()).upper())
        token = headers[b":path"][len(b"/3/device/") :].decode("ascii")
        payload = json.loads(self.body)
        if token not in server.devices:
            data = json.dumps({"apns-id": apns_id, "reason": BadDeviceToken.codename})
            return Response(
                [
                    (b":status", b"400"),
                    (b"content-length", str(len(data)).encode("ascii")),
                ],
                data.encode("utf-8"),
            )
        else:
            server.devices[token].append(payload)
            data = json.dumps({"apns-id": apns_id})
            return Response(
                [
                    (b":status", b"200"),
                    (b"apns-id", apns_id.encode("ascii")),
                    (b"content-length", b"0"),
                ],
                b"",
            )


def coroify(coro):
    @wraps(coro)
    def func(self, *args, **kwargs):
        task = asyncio.ensure_future(coro(self, *args, **kwargs))
        self.pending.append(task)
        task.add_done_callback(lambda *args: self.pending.remove(task))

    return func


class HTTP2Protocol(Protocol):
    def __init__(self, server):
        self.conn = H2Connection(H2Configuration(client_side=False))
        self.requests = {}
        self.server = server
        self.transport = None
        self.pending = []

    @coroify
    async def connection_made(self, transport):
        self.server.logger.info("connection-made", server=self.server)
        self.transport = transport
        self.conn.initiate_connection()
        await asyncio.sleep(self.server.lag)
        self.transport.write(self.conn.data_to_send())

    def connection_lost(self, exc):
        self.server.logger.info(
            "connection-lost", protocol=self, server=self.server, exc=exc
        )
        self.server.connections.remove(self)

    @coroify
    async def data_received(self, data):
        await asyncio.sleep(self.server.lag)
        events = self.conn.receive_data(data)
        for event in events:
            if isinstance(event, RequestReceived):
                self.handle_request(event.stream_id, event.headers)
            elif isinstance(event, DataReceived):
                self.handle_data(event.stream_id, event.data)
            elif isinstance(event, StreamEnded):
                self.stream_ended(event.stream_id)
        to_send = self.conn.data_to_send()
        if to_send:
            self.transport.write(to_send)

    def handle_request(self, stream_id, headers):
        self.requests[stream_id] = Request(headers)

    def handle_data(self, stream_id, data):
        self.requests[stream_id].body += data

    def stream_ended(self, stream_id):
        request = self.requests.pop(stream_id)
        response = request.handle(self.server)
        self.conn.send_headers(stream_id, response.headers)
        if response.body:
            self.conn.send_data(stream_id, response.body)
        self.conn.end_stream(stream_id)

    async def close(self):
        if self.transport is not None:
            self.transport.close()
        await asyncio.gather(*self.pending)


@attr.s
class FakeServer:
    devices = attr.ib()
    lag = attr.ib(default=0)
    server = attr.ib(default=None)
    address = attr.ib(default=None)
    connections = attr.ib(default=attr.Factory(list))

    async def stop(self):
        self.logger.msg("stopping", server=self.server)
        for connection in self.connections:
            await connection.close()
        self.server.close()
        await self.server.wait_closed()
        self.logger.msg("stopped", server=self.server)

    def create_device(self):
        device_id = secrets.token_hex(32)
        self.devices[device_id] = []
        return device_id

    def get_notifications(self, device_id):
        return self.devices[device_id]

    def create_protocol(self):
        protocol = HTTP2Protocol(self)
        self.connections.append(protocol)
        return protocol


@asynccontextmanager
async def start_fake_apns_server(port=0, database=None, lag=0):
    database = {} if database is None else database
    private_key = gen_private_key()
    certificate = gen_certificate(private_key, "server")
    with tempfile.TemporaryDirectory() as workspace:
        key_path = os.path.join(workspace, "key.pem")
        cert_path = os.path.join(workspace, "cert.pem")
        with open(key_path, "wb") as fobj:
            fobj.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
        with open(cert_path, "wb") as fobj:
            fobj.write(certificate.public_bytes(encoding=serialization.Encoding.PEM))
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        ssl_context.set_alpn_protocols(["h2"])

        fake_server = FakeServer(database, lag)

        loop = asyncio.get_event_loop()
        server = await loop.create_server(
            fake_server.create_protocol, "127.0.0.1", port, ssl=ssl_context
        )
        fake_server.address = server.sockets[0].getsockname()
        fake_server.server = server
        try:
            yield fake_server
        finally:
            await fake_server.stop()


def main():
    async def helper():
        async with start_fake_apns_server() as server:
            device_id = server.create_device()
            print(f"Serving on {server.address}")
            print(f"Device ID: {device_id}")
            while True:
                await asyncio.sleep(1)

    try:
        asyncio.get_event_loop().run_until_complete(helper())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
