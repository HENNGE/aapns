import asyncio
import json
import ssl
import warnings
from functools import wraps
from typing import Optional, Callable, Awaitable, Tuple, List, Any, Union

import attr
from pathlib import Path
from structlog import BoundLogger

from aapns.connection import APNSProtocol
from . import errors, config, models, connection


Headers = List[Tuple[str, str]]
Coroutine = Callable[..., Awaitable[Any]]


@attr.s
class Connector:
    client_cert_path: Path = attr.ib()
    ssl_context: ssl.SSLContext = attr.ib()
    logger: BoundLogger = attr.ib()
    auto_reconnect: bool = attr.ib()
    timeout: int = attr.ib()
    server: config.Server = attr.ib()
    connection: Union[None, APNSProtocol] = attr.ib(default=None)
    canceller: Union[None, asyncio.Task] = attr.ib(default=None)
    do_connect: bool = attr.ib(default=True)

    async def __aenter__(self) -> APNSProtocol:
        loop = asyncio.get_event_loop()
        task = asyncio.Task.current_task()
        if self.timeout and self.timeout > 0:
            self.canceller = loop.call_later(self.timeout, task.cancel)
        while self.connection is None:
            if not self.do_connect:
                raise errors.Disconnected()
            self.connection = APNSProtocol(
                self.server.host, self.logger, self.clear_connection
            )
            await loop.create_connection(
                lambda: self.connection,
                self.server.host,
                self.server.port,
                ssl=self.ssl_context,
            )
        return self.connection

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.canceller is not None:
            self.canceller.cancel()
        if exc_type is asyncio.CancelledError:
            raise asyncio.TimeoutError()

    def clear_connection(self):
        self.connection = None
        self.do_connect = self.auto_reconnect

    def close(self):
        if self.connection is not None:
            self.connection.close()


def encode_request(
    *,
    server: config.Server,
    token: str,
    notification: models.Notification,
    apns_id: Optional[str] = None,
    expiration: Optional[int] = None,
    priority: config.Priority = config.Priority.normal,
    topic: Optional[str] = None,
    collapse_id: Optional[str] = None,
) -> Tuple[Headers, bytes]:
    request_body = notification.encode()
    request_headers = [
        (":method", "POST"),
        (":authority", server.host),
        (":scheme", "https"),
        (":path", f"/3/device/{token}"),
        ("content-length", str(len(request_body))),
        ("apns-priority", str(priority.value)),
    ]

    if apns_id:
        request_headers.append(("apns-id", apns_id))
    if expiration:
        request_headers.append(("apns-expiration", str(expiration)))
    if topic:
        request_headers.append(("apns-topic", topic))
    if collapse_id:
        request_headers.append(("apns-collapse-id", collapse_id))
    return request_headers, request_body


def handle_response(response: connection.Response) -> str:
    response_id = response.headers.get("apns-id", "")

    if response.status != 200:
        try:
            reason = json.loads(response.body)["reason"]
        except:
            reason = response.body
        exc = errors.get(reason, response_id)
        raise exc
    else:
        return response_id


def ensure_task(coro: Coroutine) -> Coroutine:
    @wraps(coro)
    async def wrapper(*args, **kwargs):
        return await asyncio.get_event_loop().create_task(coro(*args, **kwargs))

    return wrapper


@attr.s
class APNS:
    connector: Connector = attr.ib()
    server: config.Server = attr.ib()

    @ensure_task
    async def send_notification(
        self,
        token: str,
        notification: models.Notification,
        *,
        apns_id: Optional[str] = None,
        expiration: Optional[int] = None,
        priority: config.Priority = config.Priority.normal,
        topic: Optional[str] = None,
        collapse_id: Optional[str] = None,
    ) -> str:
        request_headers, request_body = encode_request(
            token=token,
            notification=notification,
            apns_id=apns_id,
            expiration=expiration,
            priority=priority,
            topic=topic,
            collapse_id=collapse_id,
            server=self.server,
        )

        async with self.connector as conn:
            response = await conn.request(headers=request_headers, body=request_body)

        return handle_response(response)

    async def close(self):
        self.connector.close()


async def connect(
    client_cert_path: str,
    server: config.Server,
    *,
    ssl_context: Optional[ssl.SSLContext] = None,
    logger: Optional[BoundLogger] = None,
    auto_reconnect: bool = False,
    timeout: Optional[float] = None,
) -> APNS:
    if ssl_context is None:
        ssl_context: ssl.SSLContext = ssl.create_default_context()
        ssl_context.set_alpn_protocols(["h2"])
        try:
            ssl_context.set_npn_protocols(["h2"])
        except AttributeError:
            pass
    ssl_context.load_cert_chain(client_cert_path)
    connector = Connector(
        client_cert_path=client_cert_path,
        ssl_context=ssl_context,
        logger=logger,
        auto_reconnect=auto_reconnect,
        timeout=timeout,
        server=server,
    )
    return APNS(connector, server)
