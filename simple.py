import asyncio
import contextlib
import logging
import math
import ssl
from dataclasses import dataclass
from unittest.mock import patch
from typing import List, Dict, Any, Tuple

import h2.connection, h2.config, h2.settings

host = "localhost"
port = 2197
client_cert_path = ".fake-cert"

ssl_context = ssl.create_default_context()
ssl_context.options |= ssl.OP_NO_TLSv1
ssl_context.options |= ssl.OP_NO_TLSv1_1
# Old-school auth
ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_cert_path)
ssl_context.set_alpn_protocols(["h2"])

# local tests
ssl_context.load_verify_locations(cafile="tests/stress/nginx/cert.pem")

# Apple limits APN payload (data) to 4KB or 5KB, depending.
# Request header is not subject to flow control in HTTP/2
# Data is subject to framing and padding, but those are minor.
MAX_NOTIFICATION_PAYLOAD_SIZE = 5120
REQUIRED_FREE_SPACE = 6000


@dataclass
class Channel:
    sid: int
    fut: asyncio.Future
    ev: List[h2.events.Event]


class Connection:
    closed = closing = False
    bg = None
    channels: Dict[int, Channel] = None
    # upstream_queue: List[Tuple[float, Any]] = None

    def __init__(self):
        self.channels = dict()
        # self.upstream_queue = []

    async def __aenter__(self):
        self.please_write = asyncio.Event()
        self.r, self.w = await asyncio.open_connection(
            host, port, ssl=ssl_context, ssl_handshake_timeout=5
        )
        info = self.w.get_extra_info("ssl_object")
        assert info, "HTTP/2 server is required"
        proto = info.selected_alpn_protocol()
        assert proto == "h2", "Failed to negotiate HTTP/2"
        # FIXME mauybe add h2 logger with reasonable config
        self.conn = h2.connection.H2Connection(
            h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        )

        self.conn.local_settings = h2.settings.Settings(
            client=True,
            initial_values={
                h2.settings.SettingCodes.ENABLE_PUSH: 0,
                # FIXME test against real server
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: 2 ** 20,
                h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: 2 ** 16 - 1,
            },
        )

        self.conn.initiate_connection()
        # APN response body is empty (ok) or small (error)
        self.conn.increment_flow_control_window(2 ** 24)
        self.please_write.set()

        self.bgr = asyncio.create_task(self.background_read())
        self.bgw = asyncio.create_task(self.background_write())

        # FIXME we could wait for settings frame from the server,
        # to tell us how much we can actually send, as initial window is small
        return self

    @property
    def blocked(self):
        return self.closing or self.closed or self.conn.outbound_flow_control_window < REQUIRED_FREE_SPACE

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            logging.warning("FIXME implement hard quit: error, cancellation")
        self.closing = True
        if self.bgr:
            self.bgr.cancel()
            pass
        if self.bgw:
            self.bgw.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                # Is it truly necessary to wait for writer to give up?
                # It's good to prevent writer from issuing socket writes...
                await self.bgw
        self.w.close()
        try:
            await self.w.wait_closed()
        except ssl.SSLError:
            # One possible explanation:
            # We've notified the server that we are closing the connection,
            # but the sever keeps sending us data.
            logging.info("Why does this happen?", exc_info=True)
        finally:
            self.closed = True

    async def background_read(self):
        try:
            while not self.closed:
                data = await self.r.read(2 ** 16)
                if not data:
                    # FIXME cleanup, errors, etc.
                    logging.warning("read: connection is closed!")
                    return

                for event in self.conn.receive_data(data):
                    sid = getattr(event, "stream_id", None)
                    error = getattr(event, "error_code", None)
                    # slightly out of order here...
                    if sid in self.channels:
                        self.channels.ev.append(event)
                        if not self.channels.fut.done():
                            self.channels.fut.set_result(None)
                    elif sid is None and error is not None:
                        # break the connection,
                        # but give users a chance to complete
                        self.closing = True
                    else:
                        logging.warning("ignored %s %s", sid, event)

                self.please_write.set()
                # FIXME notify users about possible change to `.blocked`
        except Exception:
            logging.exception("background read task died")
            # FIXME report that connection is busted

    async def write(self, data: bytes):
        # FIXME maybe lock this... but then again, why would 2 coros
        # attempt to write at the same time?
        ...

    async def post(self, header, body, deadline=math.inf) -> Tuple[dict, bytes]:
        assert len(body) < MAX_NOTIFICATION_PAYLOAD_SIZE

        now = time.monotonic()
        if now > deadline:
            raise Timeout()
        if self.blocked:
            raise Blocked()

        sid = self.conn.get_next_available_stream_id()
        assert sid not in self.channels
        ch = self.channels[sid] = Channel(sid, None, [])
        self.conn.send_headers(sid, header, False)
        self.conn.increment_flow_control_window(2 ** 16, stream_id=sid)
        self.conn.send_data(...)

        while not self.closing:
            ch.fut = asyncio.Future()
            await asyncio.wait_for(ch.fut, deadline - now)
            logging.warning("implement")

        logging.warning("no what?")

    async def background_write(self):
        try:
            # FIXME what should writer do on `closing and not closed`?
            while not self.closed:
                data = None

                while not data:
                    if self.closed:
                        return
                    data = self.conn.data_to_send()
                    if not data:
                        await self.please_write.wait()
                        self.please_write.clear()

                self.w.write(data)
                await self.w.drain()
        except Exception:
            logging.exception("background write task died")
            # FIXME report that connection is busted


async def test():
    async with Connection() as self:
        await asyncio.sleep(1)


class Blocked(Exception): ...


class Timeout(Exception): ...


logging.basicConfig(level=logging.INFO)
asyncio.run(test())
