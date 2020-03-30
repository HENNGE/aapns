import json
import ssl
from asyncio import TimeoutError, Future, Event, open_connection, create_task, CancelledError, wait_for
from contextlib import suppress
from dataclasses import dataclass, field
from logging import getLogger
from math import inf
from time import time
from typing import Any, Dict, List, Optional, Tuple

import h2.config
import h2.connection
import h2.settings
from yarl import URL

# Apple limits APN payload (data) to 4KB or 5KB, depending.
# Request header is not subject to flow control in HTTP/2
# Data is subject to framing and padding, but those are minor.
MAX_NOTIFICATION_PAYLOAD_SIZE = 5120
REQUIRED_FREE_SPACE = 6000

# FIXME set `TCP_NOTSENT_LOWAT` on Linux
# Presumably it's on for macOS by default
# https://github.com/dabeaz/curio/issues/83


@dataclass
class Channel:
    sid: int
    fut: Optional[Future]
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
    r = w = bgr = bgw = None
    channels: Dict[int, Channel]
    max_concurrent_streams = 100  # recommended in RFC7540#section-6.5.2
    last_new_sid = last_sent_sid = -1  # client streams are odd

    def __repr__(self):
        return f"<Connection {self.state} {self.host}:{self.port} buffered:{self.buffered} inflight:{self.inflight}>"

    def __init__(self, base_url: str, ssl=None, logger=None):
        self.channels = dict()
        url = URL(base_url)
        self.host = url.host
        self.port = url.port
        self.ssl = ssl if ssl else create_ssl_context()
        self.logger = logger or getLogger("aapns")

    async def __aenter__(self):
        assert ssl.OP_NO_TLSv1 in self.ssl.options
        assert ssl.OP_NO_TLSv1_1 in self.ssl.options
        # https://bugs.python.org/issue40111 validate h2 alpn

        self.please_write = Event()
        self.r, self.w = await open_connection(
            self.host, self.port, ssl=self.ssl, ssl_handshake_timeout=5
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
                # FIXME Apple server settings:
                # HEADER_TABLE_SIZE 4096
                # MAX_CONCURRENT_STREAMS 1000
                # INITIAL_WINDOW_SIZE 65535
                # MAX_FRAME_SIZE 16384
                # MAX_HEADER_LIST_SIZE 8000
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: 2 ** 20,
                h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: 2 ** 16 - 1,
            },
        )

        self.conn.initiate_connection()
        # APN response body is empty (ok) or small (error)
        self.conn.increment_flow_control_window(2 ** 24)
        self.please_write.set()

        self.bgr = create_task(self.background_read(), name="bg-read")
        self.bgw = create_task(self.background_write(), name="bg-write")

        # FIXME we could wait for settings frame from the server,
        # to tell us how much we can actually send, as initial window is small
        return self

    @property
    def state(self):
        if not self.please_write: return "new"
        elif not self.bgw: return "starting"
        elif not self.closing: return "active"
        elif not self.closed: return "closing"
        else: return "closed"

    @property
    def buffered(self):
        """ This metric shows how "slow" we are sending requests out. """
        return (self.last_new_sid - self.last_sent_sid) // 2

    @property
    def pending(self):
        """ This metric shows how "slow" the server is to respond. """
        return len(self.channels)

    @property
    def inflight(self):
        return self.pending - self.buffered

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
            # FIXME distinguish between cancellation and context exception
            if self.bgw:
                self.bgw.cancel()
                with suppress(CancelledError):
                    await self.bgw

            if self.bgr:
                self.bgr.cancel()
                with suppress(CancelledError):
                    await self.bgr

            # at this point, we must release or cancel all pending requests
            for sid, ch in self.channels.items():
                if ch.fut and not ch.fut.done():
                    ch.fut.set_exception(Closed())

            self.w.close()
            with suppress(ssl.SSLError):
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
                    self.logger.info("APN: %s", event)

                    if isinstance(event, h2.events.RemoteSettingsChanged):
                        self.logger.debug("rcv %s", event)
                        m = event.changed_settings.get(
                            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS
                        )
                        if m:
                            self.max_concurrent_streams = m.new_value
                    elif (
                        isinstance(event, h2.events.WindowUpdated)
                        and not event.stream_id
                    ):
                        self.logger.debug("rcv %s", event)

                    sid = getattr(event, "stream_id", 0)
                    error = getattr(event, "error_code", None)
                    ch = self.channels.get(sid)
                    if ch:
                        ch.ev.append(event)
                        if not ch.fut.done():
                            ch.fut.set_result(None)
                    else:
                        # Common case: post caller timed out and quit
                        self.logger.debug("request fell off %s %s", sid, event)

                    if not sid and error is not None:
                        # FIXME break the connection,but give users a chance to complete
                        self.logger.warning("Bad error %s", event)
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
                            self.logger.debug("ignored global event %s", event)

                self.please_write.set()
                # FIXME notify users about possible change to `.blocked`
                # FIXME selective:
                # * h2.events.WindowUpdated
                # * max_concurrent_streams change
                # * [maybe] starting a stream
                # * a stream getting closed (but not half-closed)
                # * closing / closed change
        except Exception:
            self.logger.exception("background read task died")
        finally:
            self.closing = self.closed = True
            for sid, ch in self.channels.items():
                if ch.fut and not ch.fut.done():
                    ch.fut.set_exception(Closed())

    async def post(self, req: "Request") -> "Response":
        assert len(req.body) <= MAX_NOTIFICATION_PAYLOAD_SIZE

        now = time()
        if now > req.deadline:
            raise Timeout()
        if self.closing or self.closed:
            raise Closed()
        if self.blocked:
            raise Blocked()

        try:
            sid = self.conn.get_next_available_stream_id()
        except h2.NoAvailableStreamIDError:
            # As of h2-3.2.0 this is permanent.
            # FIXME ought we mark the connection as closing?
            raise Closed()

        assert sid not in self.channels

        self.last_new_sid = sid

        ch = self.channels[sid] = Channel(sid, None, [])
        self.conn.send_headers(sid, req.header, end_stream=False)
        self.conn.increment_flow_control_window(2 ** 16, stream_id=sid)
        self.conn.send_data(sid, req.body, end_stream=True)
        self.please_write.set()

        try:
            while not self.closing:
                ch.fut = Future()
                with suppress(TimeoutError):
                    await wait_for(ch.fut, req.deadline - now)
                now = time()
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
            while not self.closed:
                data = None

                while not data:
                    if self.closed:
                        return

                    if data := self.conn.data_to_send():
                        self.w.write(data)
                        last_sid = self.last_new_sid
                        await self.w.drain()
                        self.last_sent_sid = last_sid
                    else:
                        await self.please_write.wait()
                        self.please_write.clear()

        except Exception:
            self.logger.exception("background write task died")
        finally:
            self.closed = True


class Blocked(Exception):
    """This connection can't send more data at this point, can try later."""


class Closed(Exception):
    """This connection is now closed, try another."""


class Timeout(Exception):
    """The request deadline has passed."""


class FormatError(Exception):
    """Response was weird."""


def authority(url: URL):
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
            deadline = time() + timeout
        elif deadline is None:
            deadline = inf

        u = URL(url)
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

    @property
    def apns_id(self):
        return self.header.get("apns-id", None)


def create_ssl_context():
    context = ssl.create_default_context()
    context.options |= ssl.OP_NO_TLSv1
    context.options |= ssl.OP_NO_TLSv1_1
    context.set_alpn_protocols(["h2"])
    return context
