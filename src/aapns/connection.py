from __future__ import annotations

import json
from asyncio import (
    CancelledError,
    Event,
    Future,
    TimeoutError,
    create_task,
    open_connection,
    wait_for,
)
from contextlib import suppress
from dataclasses import dataclass, field
from logging import getLogger
from math import inf
from ssl import OP_NO_TLSv1, OP_NO_TLSv1_1, SSLContext, SSLError, create_default_context
from time import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import h2.config
import h2.connection
import h2.settings

from .errors import Blocked, Closed, FormatError, ResponseTooLarge, Timeout

# Apple limits APN payload (data) to 4KB or 5KB, depending.
# Request header is not subject to flow control in HTTP/2
# Data is subject to framing and padding, but those are minor.
MAX_NOTIFICATION_PAYLOAD_SIZE = 5120
REQUIRED_FREE_SPACE = 6000
# OK response is empty
# Error response is short json, ~30 bytes in size
MAX_RESPONSE_SIZE = 2 ** 16
# Inbound connection flow control window
# It's quite arbitrary, guided by:
# * concurrent requests limit, server limit being 1000 today
# * expected response size, see above
CONNECTION_WINDOW_SIZE = 2 ** 24
logger = getLogger(__package__)


@dataclass(eq=False)
class Connection:
    """Encapsulates a single HTTP/2 connection to the APN server

    Connection states:
    * new (not connected)
    * starting
    * active
    * graceful shutdown (to do)
    * closing
    * closed
    """

    host: str
    port: int
    protocol: h2.connection.H2Connection
    read_stream: Any
    write_stream: Any
    should_write: Event
    channels: Dict[int, Channel] = field(default_factory=dict)
    reader: Task = field(init=False)
    writer: Task = field(init=False)
    closed: bool = False
    closing: bool = False
    outcome: Optional[str] = None
    max_concurrent_streams: int = 100  # recommended in RFC7540#section-6.5.2
    last_new_sid: int = -1
    last_sent_sid: int = -1  # client streams are odd

    def __repr__(self):
        bits = [self.state, f"{self.host}:{self.port}"]
        if self.state != "closed":
            bits.extend([f"buffered:{self.buffered}", f"inflight:{self.inflight}"])
        else:
            bits.append(f"outcome:{self.outcome}")
        return "<Connection %s>" % " ".join(bits)

    @classmethod
    async def create(cls, origin: str, ssl: Optional[SSLContext] = None):
        url = urlparse(origin)
        assert url.scheme == "https"
        assert url.hostname
        assert not url.username
        assert not url.password
        assert not url.path
        assert not url.params
        assert not url.query
        assert not url.fragment
        host = url.hostname
        port = url.port or 443

        ssl_context = ssl if ssl else create_ssl_context()
        assert OP_NO_TLSv1 in ssl_context.options
        assert OP_NO_TLSv1_1 in ssl_context.options
        # https://bugs.python.org/issue40111 validate context h2 alpn

        protocol = h2.connection.H2Connection(
            h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        )
        protocol.local_settings = h2.settings.Settings(
            client=True,
            initial_values={
                # Apple server settings:
                # HEADER_TABLE_SIZE 4096
                # MAX_CONCURRENT_STREAMS 1000
                # INITIAL_WINDOW_SIZE 65535
                # MAX_FRAME_SIZE 16384
                # MAX_HEADER_LIST_SIZE 8000
                h2.settings.SettingCodes.ENABLE_PUSH: 0,
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: 2 ** 20,
                h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: 2 ** 16 - 1,
                h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: MAX_RESPONSE_SIZE,
            },
        )

        protocol.initiate_connection()
        protocol.increment_flow_control_window(CONNECTION_WINDOW_SIZE)

        try:
            read_stream, write_stream = await open_connection(
                host, port, ssl=ssl_context, ssl_handshake_timeout=5
            )
            info = write_stream.get_extra_info("ssl_object")
            # FIXME better exceptions
            assert info, "HTTP/2 server is required"
            proto = info.selected_alpn_protocol()
            assert proto == "h2", "Failed to negotiate HTTP/2"
        except AssertionError:
            write_stream.close()
            with suppress(SSLError):
                await write_stream.wait_closed()
            raise

        should_write = Event()
        should_write.set()

        # FIXME we could wait for settings frame from the server,
        # to tell us how much we can actually send, as initial window is small
        return cls(host, port, protocol, read_stream, write_stream, should_write)

    def __post_init__(self):
        self.reader = create_task(self.background_read(), name="bg-read")
        self.writer = create_task(self.background_write(), name="bg-write")

    @property
    def state(self):
        if not self.should_write:
            return "new"
        elif not self.writer:
            return "starting"
        elif not self.closing:
            return "active"
        elif not self.closed:
            return "closing"
        else:
            return "closed"

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
            or self.protocol.outbound_flow_control_window <= REQUIRED_FREE_SPACE
            # FIXME accidentally quadratic: .openxx iterates over all streams
            # could be kinda fixed by caching with clever invalidation...
            or self.protocol.open_outbound_streams >= self.max_concurrent_streams
        )

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def close(self):
        self.closing = True
        if not self.outcome:
            self.outcome = "Closed"
        try:
            # FIXME distinguish between cancellation and context exception
            if self.writer:
                self.writer.cancel()
                with suppress(CancelledError):
                    await self.writer

            if self.reader:
                self.reader.cancel()
                with suppress(CancelledError):
                    await self.reader

            # at this point, we must release or cancel all pending requests
            for sid, ch in self.channels.items():
                if ch.fut and not ch.fut.done():
                    ch.fut.set_exception(Closed(self.outcome))

            self.write_stream.close()
            with suppress(SSLError):
                await self.write_stream.wait_closed()
        finally:
            self.closed = True

    async def background_read(self):
        try:
            while not self.closed:
                data = await self.read_stream.read(2 ** 16)
                if not data:
                    break

                for event in self.protocol.receive_data(data):
                    logger.debug("APN: %s", event)
                    sid = getattr(event, "stream_id", 0)
                    error = getattr(event, "error_code", None)
                    ch = self.channels.get(sid)

                    if isinstance(event, h2.events.RemoteSettingsChanged):
                        m = event.changed_settings.get(
                            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS
                        )
                        if m:
                            self.max_concurrent_streams = m.new_value
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        self.closing = True
                        if not self.outcome:
                            if event.additional_data:
                                try:
                                    self.outcome = json.loads(
                                        event.additional_data.decode("utf-8")
                                    )["reason"]
                                except Exception:
                                    self.outcome = str(event.additional_data[:100])
                            else:
                                self.outcome = str(event.error_code)
                        logger.info("%s %s", self, self.outcome)
                    elif not sid and error is not None:
                        # FIXME break the connection,but give users a chance to complete
                        logger.warning("Bad error %s", event)
                        self.outcome = str(error)
                        self.closing = True
                    else:
                        if isinstance(event, h2.events.DataReceived):
                            # Stream flow control is responsibility of the channel.
                            # Connection flow control is handled here.
                            self.protocol.increment_flow_control_window(
                                event.flow_controlled_length
                            )
                        if ch:
                            ch.ev.append(event)
                            if not ch.fut.done():
                                ch.fut.set_result(None)

                # Somewhat inefficient: wake up background writer just in case
                # it could be that we've received something that h2 needs to acknowledge
                self.should_write.set()

                # FIXME notify users about possible change to `.blocked`
                # FIXME selective:
                # * h2.events.WindowUpdated
                # * max_concurrent_streams change
                # * [maybe] starting a stream
                # * a stream getting closed (but not half-closed)
                # * closing / closed change
        except ConnectionError as e:
            if not self.outcome:
                self.outcome = str(e)
        except Exception:
            logger.exception("background read task died")
        finally:
            self.closing = self.closed = True
            for sid, ch in self.channels.items():
                if ch.fut and not ch.fut.done():
                    ch.fut.set_exception(Closed(self.outcome))

    async def post(self, req: "Request") -> "Response":
        assert len(req.body) <= MAX_NOTIFICATION_PAYLOAD_SIZE

        now = time()
        if now > req.deadline:
            raise Timeout("Request timed out")
        if self.closing or self.closed:
            raise Closed(self.outcome)
        if self.blocked:
            raise Blocked()

        try:
            sid = self.protocol.get_next_available_stream_id()
        except h2.NoAvailableStreamIDError:
            self.closing = True
            if not self.outcome:
                self.outcome = "Exhausted"
            raise Closed(self.outcome)

        assert sid not in self.channels

        self.last_new_sid = sid

        ch = self.channels[sid] = Channel(sid, None, [])
        self.protocol.send_headers(
            sid, req.header_with(self.host, self.port), end_stream=False
        )
        # FIXME don't we have to increment global flow control
        # also on reception of stream X data?
        self.protocol.increment_flow_control_window(MAX_RESPONSE_SIZE, stream_id=sid)
        self.protocol.send_data(sid, req.body, end_stream=True)
        self.should_write.set()

        try:
            while not self.closed:
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
                    elif len(ch.body) >= MAX_RESPONSE_SIZE:
                        raise ResponseTooLarge(f"Larger than {MAX_RESPONSE_SIZE}")
            else:
                raise Closed(self.outcome)
        finally:
            # FIXME reset the stream, if:
            # * connection is still alive, and
            # * the stream didn't end yet
            del self.channels[sid]

    async def background_write(self):
        try:
            while not self.closed:
                data = None

                while not data:
                    if self.closed:
                        return

                    if data := self.protocol.data_to_send():
                        self.write_stream.write(data)
                        last_sid = self.last_new_sid
                        await self.write_stream.drain()
                        self.last_sent_sid = last_sid
                    else:
                        await self.should_write.wait()
                        self.should_write.clear()

        except ConnectionError as e:
            if not self.outcome:
                self.outcome = str(e)
        except Exception:
            logger.exception("background write task died")
        finally:
            self.closing = self.closed = True


@dataclass
class Channel:
    sid: int
    fut: Optional[Future]
    ev: List[h2.events.Event] = field(default_factory=list)
    header: Optional[dict] = None
    body: bytes = b""


@dataclass
class Request:
    header: tuple
    body: bytes
    deadline: float

    def header_with(self, host: str, port: int) -> tuple:
        """Request header including :authority pseudo header field for target server"""
        return ((":authority", f"{host}:{port}"),) + self.header

    @classmethod
    def new(
        cls,
        path: str,
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

        assert path.startswith("/")
        pseudo = dict(method="POST", scheme="https", path=path)
        h = tuple((f":{k}", v) for k, v in pseudo.items()) + tuple(
            (header or {}).items()
        )
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
    context = create_default_context()
    context.options |= OP_NO_TLSv1
    context.options |= OP_NO_TLSv1_1
    context.set_alpn_protocols(["h2"])
    return context
