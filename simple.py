import asyncio
import contextlib
import json
import logging
import math
import random
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import h2.config
import h2.connection
import h2.settings
import yarl

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
    fut: Optional[asyncio.Future]
    ev: List[h2.events.Event] = field(default_factory=list)
    header: Optional[dict] = None
    body: bytes = b""


class Connection:
    # FIXME document connection termination states:
    # * not closing: active connection
    # * closing and not closed: graceful shutdown
    # * closed: totally dead
    # * fixme: __aexit__() finished
    closed = closing = False
    bg = None
    channels: Dict[int, Channel]
    max_concurrent_streams = 100  # recommended in RFC7540#section-6.5.2

    def __init__(self, base_url: str):
        self.channels = dict()
        url = yarl.URL(base_url)
        self.host = url.host
        self.port = url.port

    async def __aenter__(self):
        self.please_write = asyncio.Event()
        self.r, self.w = await asyncio.open_connection(
            self.host, self.port, ssl=ssl_context, ssl_handshake_timeout=5
        )
        info = self.w.get_extra_info("ssl_object")
        assert info, "HTTP/2 server is required"
        proto = info.selected_alpn_protocol()
        assert proto == "h2", "Failed to negotiate HTTP/2"

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
        return (
            self.closing
            or self.closed
            or self.conn.outbound_flow_control_window <= REQUIRED_FREE_SPACE
            # FIXME accidentally quadratic: .openxx iterates over all streams
            # could be kinda fixed by caching with clever invalidation...
            or self.conn.open_outbound_streams >= self.max_concurrent_streams
        )

    async def __aexit__(self, exc_type, exc, tb):
        self.closing = True
        try:
            if isinstance(exc_type, asyncio.CancelledError):
                logging.warning("FIXME implement cancellation")
            elif exc_type:
                # hard quit: code block raised exception
                # it may be that the connection is already closed
                # hopefully error handling can be same
                pass

            if self.bgw:
                self.bgw.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.bgw

            if self.bgr:
                self.bgr.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.bgr

            # at this point, we must release or cancel all pending requests
            for sid, ch in self.channels.items():
                if ch.fut and not ch.fut.done():
                    ch.fut.set_exception(Closed())

            self.w.close()
            with contextlib.suppress(ssl.SSLError):
                await self.w.wait_closed()
        finally:
            self.closed = True

    async def background_read(self):
        try:
            while not self.closed:
                data = await self.r.read(2 ** 16)
                if not data:
                    break

                for event in self.conn.receive_data(data):
                    logging.info("APN: %s", event)

                    if isinstance(event, h2.events.RemoteSettingsChanged):
                        logging.debug("rcv %s", event)
                        m = event.changed_settings.get(
                            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS
                        )
                        if m:
                            self.max_concurrent_streams = m.new_value
                    elif (
                        isinstance(event, h2.events.WindowUpdated)
                        and not event.stream_id
                    ):
                        logging.debug("rcv %s", event)

                    sid = getattr(event, "stream_id", 0)
                    error = getattr(event, "error_code", None)
                    ch = self.channels.get(sid)
                    if ch:
                        ch.ev.append(event)
                        if not ch.fut.done():
                            ch.fut.set_result(None)
                    else:
                        # Common case: post caller timed out and quit
                        logging.debug("request fell off %s %s", sid, event)

                    if not sid and error is not None:
                        # FIXME break the connection,but give users a chance to complete
                        logging.warning("Bad error %s", event)
                        self.closing = True
                    elif not sid:
                        if not isinstance(
                            event,
                            (
                                h2.events.RemoteSettingsChanged,
                                h2.events.SettingsAcknowledged,
                                h2.events.WindowUpdated,
                            ),
                        ):
                            logging.debug("ignored global event %s", event)

                self.please_write.set()
                # FIXME notify users about possible change to `.blocked`
                # FIXME selective:
                # * h2.events.WindowUpdated
                # * max_concurrent_streams change
                # * [maybe] starting a stream
                # * a stream getting closed (but not half-closed)
                # * closing / closed change
            else:
                self.closing = self.closed = True
                for sid, ch in self.channels.items():
                    if ch.fut and not ch.fut.done():
                        ch.fut.set_exception(Closed())
        except Exception:
            logging.exception("background read task died")
            # FIXME report that connection is busted

    async def post(self, req: "Request") -> "Response":
        assert len(req.body) <= MAX_NOTIFICATION_PAYLOAD_SIZE

        now = time.monotonic()
        if now > req.deadline:
            raise Timeout()
        if self.blocked:
            raise Blocked()

        try:
            sid = self.conn.get_next_available_stream_id()
        except h2.NoAvailableStreamIDError:
            # As of h2-3.2.0 this is permanent.
            # FIXME ought we mark the connection as closing?
            raise Closed()

        assert sid not in self.channels

        ch = self.channels[sid] = Channel(sid, None, [])
        self.conn.send_headers(sid, req.header, end_stream=False)
        self.conn.increment_flow_control_window(2 ** 16, stream_id=sid)
        self.conn.send_data(sid, req.body, end_stream=True)
        self.please_write.set()

        try:
            while not self.closing:
                ch.fut = asyncio.Future()
                try:
                    await asyncio.wait_for(ch.fut, req.deadline - now)
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                if now > req.deadline:
                    raise Timeout()
                for event in ch.ev:
                    if isinstance(event, h2.events.ResponseReceived):
                        ch.header = dict(event.headers)
                    elif isinstance(event, h2.events.DataReceived):
                        ch.body += event.data
                    elif isinstance(event, h2.events.StreamEnded):
                        return Response.new(ch.header, ch.body)
            else:
                raise Closed()
        finally:
            del self.channels[sid]

    async def background_write(self):
        try:
            # FIXME what should writer do on `closing and not closed`?
            while not self.closed:
                data = None

                while not data:
                    if self.closed:
                        return

                    if data := self.conn.data_to_send():
                        self.w.write(data)
                        await self.w.drain()
                    else:
                        await self.please_write.wait()
                        self.please_write.clear()

        except Exception:
            logging.exception("background write task died")
            # FIXME report that connection is busted


class Blocked(Exception):
    """This connection can't send more data at this point, can try later."""


class Closed(Exception):
    """This connection is now closed, try another."""


class Timeout(Exception):
    """The request deadline has passed."""


class FormatError(Exception):
    """Response was weird."""


def authority(url: yarl.URL):
    if (
        url.port is None
        or url.scheme == "http"
        and url.port == 80
        or url.scheme == "https"
        and url.port == 443
    ):
        return url.host
    else:
        return f"{url.host}:{url.port}"


class Pool:
    """Super-silly, fixed-size connection pool"""

    closing = closed = False
    base_url = None
    size = 10
    conn = None
    dying = None

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.conn = set()
        self.dying = set()

    async def background_resize(self):
        while True:
            if self.closing or self.closed:
                return

            for c in list(self.conn):
                if c.closing:
                    self.conn.remove(c)
                    self.dying.add(c)

            while len(self.conn) > self.size:
                c = self.conn.pop()
                c.closing = True
                self.dying.add(c)

            for c in list(self.dying):
                if c.closed:
                    self.dying.remove(c)
                elif not c.channels:
                    self.dying.remove(c)
                    await c.__aexit__(None, None, None)

            while len(self.conn) < self.size:
                c = Connection(self.base_url)
                try:
                    await c.__aenter__()
                    self.conn.add(c)
                except Exception:
                    logging.exception("New connection failed")
                    break

            # FIXME a way to trigger resize
            await asyncio.sleep(1)

    async def __aenter__(self):
        self.bg = asyncio.create_task(self.background_resize())
        self.conn = [Connection(self.base_url) for i in range(self.size)]
        await asyncio.gather(*(c.__aenter__() for c in self.conn))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closing = True
        try:
            if self.bg:
                self.bg.cancel()
                await self.bg

            await asyncio.gather(
                    *(c.__aexit__(exc_type, exc, tb) for c in self.conn),
                    *(c.__aexit__(exc_type, exc, tb) for c in self.dying),
                    )
        finally:
            self.closed = True

    async def post(self, req: "Request") -> "Response":
        random.shuffle(self.conn)
        # FIXME handle connection getting closed
        # FIXME handle connection replacement
        for c in self.conn:
            # FIXME super-inefficient, as `.blocked` is already pretty bad
            if not c.blocked:
                break
        else:
            raise Blocked()
        return await c.post(req)


@dataclass
class Request:
    header: tuple
    body: bytes
    deadline: float

    @classmethod
    def new(
        cls,
        url: str,
        header: Optional[dict],
        data: dict,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ):
        if timeout is not None and deadline is not None:
            raise ValueError("Specify timeout or deadline, but not both")
        elif timeout is not None:
            deadline = time.monotonic() + timeout
        elif deadline is None:
            deadline = math.inf

        u = yarl.URL(url)
        pseudo = dict(
            method="POST", scheme=u.scheme, authority=authority(u), path=u.path_qs
        )
        h = tuple((f":{k}", v) for k, v in pseudo.items()) + tuple(
            (header or {}).items()
        )
        # aapns:master sends this header
        #   [Host], Accept, Accept-Encoding, Apns-Priority, Apns-Push-Type, Content-Length, User-Agent
        # this branch sends this header
        #   [Host], Apns-Priority, Apns-Push-Type
        #
        # FIXME 1.
        # Does APN require Content-Length header field?
        # It's optional in HTTP/2... Does Apple need it?
        #
        # FIXME 2.
        # Apns-Expiration should be derived from `deadline`
        return cls(h, json.dumps(data, ensure_ascii=False).encode("utf-8"), deadline)


@dataclass
class Response:
    code: int
    header: Dict[str, str]
    data: Optional[dict]

    @classmethod
    def new(cls, header: Optional[dict], body: bytes):
        h = {**(header or {})}
        code = int(h.pop(":status", "0"))
        try:
            return cls(code, h, json.loads(body) if body else None)
        except json.JSONDecodeError:
            raise FormatError(f"Not JSON: {body[:20]!r}")


import collections
import pytest

stats = collections.defaultdict(int)

pytestmark = pytest.mark.asyncio


async def one_request(c, i):
    try:
        req = Request.new(
            f"https://localhost:2197/3/device/aaa-{i}",
            dict(foo="bar"),
            dict(baz=42),
            timeout=min(i * 0.1, 10),
        )
        resp = await c.post(req)
        # logging.info("%s %s %s", i, resp.code, resp.data)
        stats[resp.code] += 1
    except (Timeout, Blocked, Closed) as e:
        # logging.info("%s %r", i, e)
        stats[repr(e)] += 1


async def test_many(count=30000):
    try:
        async with Pool("https://localhost:2197") as c:
            # FIXME how come it's worse with the initial wait?
            await asyncio.sleep(0.1)
            await asyncio.gather(*[one_request(c, i) for i in range(-2, count, 2)])
    except Closed:
        logging.warning("Oops, closed")
    finally:
        print(stats)


async def send_several(base_url, requests):
    try:
        async with Connection(base_url) as c:
            tasks = []
            for r in requests:
                logging.info("Sleeping a bit")
                await asyncio.sleep(1)

                async def post(r):
                    try:
                        logging.info("%s", r)
                        logging.info("%s", await c.post(r))
                    except Exception as rv:
                        logging.info("%s", rv)

                # FIXME 2. Optional header fields
                # Apns-Id, Apns-Expiration, Apns-Topic, Apns-Collapse-Id
                tasks.append(asyncio.create_task(post(r)))
            await asyncio.gather(*tasks)
    finally:
        # print(stats)
        pass


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    # performance test
    # asyncio.run(test_many(**({"count": int(sys.argv[1])} if len(sys.argv) > 1 else {})))

    ssl_context = ssl.create_default_context()
    ssl_context.options |= ssl.OP_NO_TLSv1
    ssl_context.options |= ssl.OP_NO_TLSv1_1
    ssl_context.set_alpn_protocols(["h2"])

    argv = sys.argv[1:]
    if "--prod" in argv:
        argv.remove("--prod")
        host = "api.push.apple.com"
    elif "--sandbox" in argv:
        argv.remove("--sandbox")
        host = "api.development.push.apple.com"
    elif "--local" in argv:
        argv.remove("--local")
        host = "localhost"
        ssl_context.load_verify_locations(cafile="tests/stress/nginx/cert.pem")
    else:
        raise Exception("Must pass flag: --prod/--sandbox/--local")

    if "--alt-port" in argv:
        argv.remove("--alt-port")
        port = 2197
    else:
        port = 443

    if "simple" in argv:
        argv.remove("simple")
        key = "body"
    elif "localized" in argv:
        argv.remove("localized")
        key = "loc-key"
    else:
        raise Exception("Must pass command: simple or localized")

    if "--client-cert-path" in argv:
        i = argv.index("--client-cert-path")
        cert = argv[i+1]
        del argv[i:i+2]
        ssl_context.load_cert_chain(certfile=cert, keyfile=cert)
    else:
        raise Exception("Must pass flag: --client-cert-path path-file.pem")

    if not argv:
        raise Exception("Must pass device_token")
    device_token = argv[0]
    del argv[0]

    if not argv:
        raise Exception("Usage: python simple.py [opts] device_token cmd message-1 message-2 ...")

    base_url = f"https://{host}:{port}"
    requests = [Request.new(
                    f"{base_url}/3/device/{device_token}",
                    {"Apns-Priority": "5", "Apns-Push-Type": "alert"},
                    {"aps": {"alert": {key: text}}},
                    timeout=10,
                ) for text in argv]

    asyncio.run(send_several(base_url, requests))
