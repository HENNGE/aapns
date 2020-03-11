import asyncio
import logging
import math
import ssl
from dataclasses import dataclass
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


@dataclass
class Channel:
    sid: int
    fut: asyncio.Future
    ev: List[h2.events.Event]


class Self:
    closed = closing = False
    bg = None
    channels: Dict[int, Channel] = None
    # upstream_queue: List[Tuple[float, Any]] = None

    def __init__(self):
        self.channels = dict()
        # self.upstream_queue = []

    async def __aenter__(self):
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
        self.conn.increment_flow_control_window(2 ** 24)  # ?
        # Probably no need for timeout, smaller than TCP buffer
        self.w.write(self.conn.data_to_send())
        await self.w.drain()

        self.bg = asyncio.create_task(self.background())

        # FIXME perhaps wait for settings frame from server
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closing = True
        if self.bg:
            self.bg.cancel()
        self.w.close()
        await self.w.wait_closed()
        self.closed = True

    async def background(self):
        while not self.closed:
            data = await self.r.read(2 ** 16)
            if not data:
                # FIXME cleanup, errors, etc.
                return
            events = self.conn.receive_data(data)
            for event in events:
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
                    logging.warn("ignored %s %s", sid, event)

    async def write(self, data: bytes):
        # FIXME maybe lock this... but then again, why would 2 coros
        # attempt to write at the same time?
        ...

    async def post(self, header, body, deadline=math.inf) -> Tuple[dict, bytes]:
        now = time.monotonic()
        if self.closing:
            raise Exception("too late")
        if now > deadline:
            raise Exception("timeout")

        # FIXME must block here if outgoing queue is full
        await self.ensure_upstream(now, deadline)

        sid = self.conn.get_next_available_stream_id()
        assert sid not in self.channels
        ch = self.channels[sid] = Channel(sid, None, [])
        self.conn.send_headers(sid, header, False)
        self.conn.increment_flow_control_window(2 ** 16, stream_id=sid)
        self.wakeup_sender()

        while True:
            ch.fut = asyncio.Future()
            await asyncio.wait_for(ch.fut, deadline - now)

    async def ensure_upstream(self, timestamp, deadline):
        """
        * if upstream has space, return immediately
        * if upstream is stuck, wait
        * background writer task will take first element
          * TBD what if multiple elements can go?
        * if a deadline hits:
        """
        if not blocked:
            return
        fut = asyncio.Future()
        me = (fut, timestamp, deadline)
        # heapq.heappush(self.upstream_queue, (timestamp, me))
        # heapq.heappush(self.deadline_queue, (deadline, me))
        await fut
        # ...

    def wakeup_sender():
        ...

    async def sender_loop():
        self.upstream_stuck = True
        self.w.write(self.conn.data_to_send())
        await self.w.drain()
        self.upstream_stuck = False


async def test():
    async with Self() as self:
        await asyncio.sleep(1)


logging.basicConfig(level=logging.INFO)
asyncio.run(test())
