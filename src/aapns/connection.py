from __future__ import annotations

import asyncio
import json
import ssl
from asyncio import CancelledError, TimeoutError, create_task, open_connection, wait_for
from contextlib import suppress
from dataclasses import dataclass, field
from logging import getLogger
from math import inf
from ssl import OP_NO_TLSv1, OP_NO_TLSv1_1, SSLError, create_default_context
from time import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import h2.config
import h2.connection
import h2.events
import h2.exceptions
import h2.settings

from .errors import Blocked, Closed, FormatError, ResponseTooLarge, StreamReset, Timeout

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
CONNECTION_WINDOW_SIZE = 1000 * MAX_RESPONSE_SIZE
# Connection establishment safety time limits
CONNECTION_TIMEOUT = 5
TLS_TIMEOUT = 5
logger = getLogger(__package__)


@dataclass(eq=False)
class Connection:
    """Encapsulates a single HTTP/2 connection to the APN server.

    Use `Connection.create(...)` to make connections.

    Example use:

        conn = await Connection.create(...)
        try:
            await conn.post(request)
        finally:
            await conn.close()

    Connection states:
    * new (not connected)
    * starting
    * active
    * graceful shutdown (to do: https://github.com/python-hyper/hyper-h2/issues/1181)
    * closing
    * closed
    """

    host: str
    port: int
    protocol: h2.connection.H2Connection
    read_stream: asyncio.StreamReader
    write_stream: asyncio.StreamWriter
    should_write: asyncio.Event = field(init=False)
    channels: Dict[int, Channel] = field(default_factory=dict)
    reader: asyncio.Task = field(init=False)
    writer: asyncio.Task = field(init=False)
    closing: bool = False
    closed: bool = False
    outcome: Optional[str] = None
    max_concurrent_streams: int = 100  # initial per RFC7540#section-6.5.2
    last_stream_id_got: int = -1
    last_stream_id_sent: int = -1  # client streams are odd

    @classmethod
    async def create(
        cls, origin: str, ssl: Optional[ssl.SSLContext] = None
    ) -> Connection:
        """Connect to `origin` and return a Connection"""
        url = urlparse(origin)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.params
            or url.query
            or url.fragment
        ):
            raise ValueError("Origin must be https://<host>[:<port>]")

        host = url.hostname
        port = url.port or 443

        ssl_context = ssl if ssl else create_ssl_context()
        if (
            OP_NO_TLSv1 not in ssl_context.options  # type: ignore # https://github.com/python/typeshed/issues/3920
            or OP_NO_TLSv1_1 not in ssl_context.options  # type: ignore
        ):
            raise ValueError("SSL Context cannot allow TLS 1.0 or 1.1")

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

        read_stream, write_stream = await wait_for(
            open_connection(
                host, port, ssl=ssl_context, ssl_handshake_timeout=TLS_TIMEOUT
            ),
            CONNECTION_TIMEOUT,
        )
        try:
            info = write_stream.get_extra_info("ssl_object")
            if not info:
                raise Closed("Failed TLS handshake")
            proto = info.selected_alpn_protocol()
            if proto != "h2":
                raise Closed("Failed to negotiate HTTP/2")
        except Closed:
            write_stream.close()
            with suppress(SSLError, ConnectionError):
                await write_stream.wait_closed()
            raise

        # FIXME we could wait for settings frame from the server,
        # to tell us how much we can actually send, as initial window is small
        return cls(host, port, protocol, read_stream, write_stream)

    def __post_init__(self):
        self.should_write = asyncio.Event()
        self.should_write.set()
        self.reader = create_task(self.background_read(), name="bg-read")
        self.writer = create_task(self.background_write(), name="bg-write")

    async def post(self, request: "Request") -> "Response":
        """Post the `request` on the connection"""
        if len(request.body) > MAX_NOTIFICATION_PAYLOAD_SIZE:
            raise ValueError("Request body is too large")

        request.get_time_left_or_fail()
        if self.closing or self.closed:
            raise Closed(self.outcome)
        if self.blocked:
            raise Blocked()

        try:
            stream_id = self.protocol.get_next_available_stream_id()
        except h2.exceptions.NoAvailableStreamIDError:
            self.closing = True
            if not self.outcome:
                self.outcome = "Exhausted"
            raise Closed(self.outcome)

        assert stream_id not in self.channels

        self.last_stream_id_got = stream_id

        self.channels[stream_id] = channel = Channel()
        self.protocol.send_headers(
            stream_id, request.header_with(self.host, self.port), end_stream=False
        )
        self.protocol.send_data(stream_id, request.body, end_stream=True)
        self.should_write.set()

        try:
            while not self.closed:
                remaining = request.get_time_left_or_fail()
                channel.wakeup.clear()
                with suppress(TimeoutError):
                    await wait_for(channel.wakeup.wait(), remaining)
                for event in channel.events:
                    if isinstance(event, h2.events.ResponseReceived):
                        channel.header = dict(event.headers)
                    elif isinstance(event, h2.events.DataReceived):
                        channel.body += event.data
                        if len(channel.body) >= MAX_RESPONSE_SIZE:
                            raise ResponseTooLarge(f"Larger than {MAX_RESPONSE_SIZE}")
                    elif isinstance(event, h2.events.StreamEnded):
                        return Response.new(channel.header, channel.body)
                    elif isinstance(event, h2.events.StreamReset):
                        raise StreamReset()
                del channel.events[:]
            raise Closed(self.outcome)
        finally:
            # FIXME reset the stream, if:
            # * connection is still alive, and
            # * this stream did not end yet
            # Must be very careful not to break the connection
            # self.protocol.reset_stream(stream_id, 0)
            # self.should_write.set()
            del self.channels[stream_id]

    async def close(self):
        """Terminate the connection and free up the resources"""
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

            self.closed = True
            self.should_write.set()

            # at this point, we must release or cancel all pending requests
            for channel in self.channels.values():
                channel.wakeup.set()

            self.write_stream.close()
            with suppress(SSLError, ConnectionError):
                await self.write_stream.wait_closed()
        finally:
            self.closed = True
            self.should_write.set()

    @property
    def state(self):
        return (
            "closed"
            if self.closed
            else "closing"
            if self.closing
            else "active"
            if self.writer
            else "starting"
            if self.should_write
            else "new"
        )

    @property
    def inflight(self):
        """Count of the requests that were sent out and are awaiting server response."""
        return self.pending - self.buffered

    @property
    def buffered(self):
        """Count of the requests that we are still to send out."""
        return (self.last_stream_id_got - self.last_stream_id_sent) // 2

    @property
    def pending(self):
        """Total count of pending requests."""
        return len(self.channels)

    @property
    def blocked(self):
        """Is this connection unable to process more requests, either for now or permanently?"""
        return (
            self.closing
            or self.closed
            or self.protocol.outbound_flow_control_window <= REQUIRED_FREE_SPACE
            # FIXME accidentally quadratic: .openxx iterates over all streams
            # could be kinda fixed by caching with clever invalidation...
            or self.protocol.open_outbound_streams >= self.max_concurrent_streams
        )

    async def background_read(self):
        try:
            while not self.closed:
                data = await self.read_stream.read(2 ** 16)
                if not data:
                    raise ConnectionError("Server closed the connection")

                for event in self.protocol.receive_data(data):
                    logger.debug("APN: %s", event)
                    stream_id = getattr(event, "stream_id", 0)
                    error = getattr(event, "error_code", None)
                    channel = self.channels.get(stream_id)

                    if isinstance(event, h2.events.RemoteSettingsChanged):
                        m = event.changed_settings.get(
                            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS
                        )
                        if m:
                            self.max_concurrent_streams = m.new_value
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        # When Apple is not happy with the whole connection,
                        # it sends smth like {"reason": "BadCertificateEnvironment"}
                        # Catch it here, so that connection pool can be invalidated.
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
                        logger.info("Closing with %s", self.outcome)
                    elif not stream_id and error is not None:
                        logger.warning("Caught off guard: %s", event)
                        raise ConnectionError(str(error))
                    else:
                        if isinstance(event, h2.events.DataReceived):
                            # Stream flow control is responsibility of the channel.
                            # Connection flow control is handled here.
                            self.protocol.acknowledge_received_data(
                                event.flow_controlled_length, stream_id
                            )
                        if channel:
                            channel.events.append(event)
                            channel.wakeup.set()

                # Somewhat inefficient: wake up background writer just in case
                # it could be that we've received something that h2 needs to acknowledge
                self.should_write.set()

                # FIXME notify pool users about possible change to `.blocked`
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
            self.should_write.set()
            for channel in self.channels.values():
                channel.wakeup.set()

    async def background_write(self):
        try:
            while not self.closed:
                data = None

                while not data:
                    if self.closed:
                        return

                    if data := self.protocol.data_to_send():
                        self.write_stream.write(data)
                        last_stream_id = self.last_stream_id_got
                        await self.write_stream.drain()
                        self.last_stream_id_sent = last_stream_id
                    else:
                        await self.should_write.wait()
                        self.should_write.clear()

        except (SSLError, ConnectionError) as e:
            if not self.outcome:
                self.outcome = str(e)
        except Exception:
            logger.exception("background write task died")
        finally:
            self.closing = self.closed = True


@dataclass
class Channel:
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    events: List[h2.events.Event] = field(default_factory=list)
    header: Optional[dict] = None
    body: bytes = b""


@dataclass
class Request:
    header: tuple
    body: bytes
    deadline: float
    deadline_source: str

    def header_with(self, host: str, port: int) -> tuple:
        """Request header including :authority pseudo header field for target server"""
        return ((":authority", f"{host}:{port}"),) + self.header

    def get_time_left_or_fail(self) -> float:
        """Raises Timeout() if the request has timed out, or return remaining time"""
        if (remaining := self.deadline - time()) > 0:
            return remaining
        raise Timeout("Request timed out: %s" % self.deadline_source)

    @classmethod
    def new(
        cls,
        path: str,
        header: Dict[str, str],
        data: dict,
        timeout: Optional[float] = 10,
        deadline: Optional[float] = None,
        expiration: Optional[float] = None,
    ) -> Request:
        if not path.startswith("/"):
            raise ValueError("Absolute URL path is required")

        deadlines = {"not set": inf}
        if timeout is not None:
            deadlines["timeout"] = time() + timeout
        if deadline is not None:
            deadlines["deadline"] = deadline
        if expiration is not None:
            deadlines["expiration"] = expiration
        deadline = min(deadlines.values())
        deadline_source = [name for name, v in deadlines.items() if v == deadline][0]

        request_header = (
            (":method", "POST"),
            (":scheme", "https"),
            (":path", path),
            *header.items(),
        )

        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return cls(request_header, text.encode("utf-8"), deadline, deadline_source)


@dataclass
class Response:
    code: int
    header: Dict[str, str]
    data: Optional[dict]

    @classmethod
    def new(cls, header: Optional[dict], body: bytes) -> Response:
        head = {**(header or {})}
        code = int(head.pop(":status", "0"))
        try:
            return cls(code, head, json.loads(body) if body else None)
        except json.JSONDecodeError:
            raise FormatError(f"Not JSON: {body[:20]!r}")

    @property
    def apns_id(self) -> Optional[str]:
        return self.header.get("apns-id", None)

    @property
    def reason(self) -> Optional[str]:
        """Response JSON 'reason' value, used in error responses."""
        return self.data.get("reason") if self.data else None


def create_ssl_context() -> ssl.SSLContext:
    """A basic SSL context suitable for HTTP/2 and APN."""
    context = create_default_context()
    context.options |= OP_NO_TLSv1
    context.options |= OP_NO_TLSv1_1
    context.set_alpn_protocols(["h2"])
    return context
