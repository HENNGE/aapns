import json
import socket
import ssl
from typing import *

import attr
from httpx import URL, AsyncClient, Response
from httpx.config import DEFAULT_TIMEOUT_CONFIG, TimeoutTypes
from structlog import BoundLogger

from . import config, errors, models
from .pool import Pool

Headers = List[Tuple[str, str]]


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
) -> Tuple[URL, Headers, bytes]:
    request_body = notification.encode()
    request_headers = [
        ("apns-priority", str(priority.value)),
        ("apns-push-type", notification.push_type.value),
    ]

    if apns_id:
        request_headers.append(("apns-id", apns_id))
    if expiration:
        request_headers.append(("apns-expiration", str(expiration)))
    if topic:
        request_headers.append(("apns-topic", topic))
    if collapse_id:
        request_headers.append(("apns-collapse-id", collapse_id))
    return (
        URL(f"https://{server.host}:{server.port}/3/device/{token}"),
        request_headers,
        request_body,
    )


async def handle_response(response: Response) -> str:
    response_id = response.headers.get("apns-id", "")

    if response.status_code != 200:
        reason = await response.aread()
        try:
            reason = json.loads(reason)["reason"]
        except Exception:
            pass
        exc = errors.get(reason, response_id)
        raise exc
    else:
        return response_id


@attr.s(auto_attribs=True, frozen=True)
class APNS:
    client: AsyncClient
    logger: BoundLogger
    server: config.Server
    pool: Pool

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
        url, request_headers, request_body = encode_request(
            token=token,
            notification=notification,
            apns_id=apns_id,
            expiration=expiration,
            priority=priority,
            topic=topic,
            collapse_id=collapse_id,
            server=self.server,
        )

        response = await self.client.post(
            url, data=request_body, headers=request_headers
        )

        return await handle_response(response)

    async def close(self):
        await self.client.aclose()
        await self.pool.__aexit__(None, None, None)


async def create_client(
    client_cert_path: str,
    server: config.Server,
    *,
    logger: Optional[BoundLogger] = None,
    timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
) -> APNS:
    client = AsyncClient(
        http2=True,
        cert=client_cert_path,
        timeout=timeout,
        verify=False,  # FIXME local testing only
    )
    base_url = "FIXME"
    apns = APNS(client, logger, server, Pool(base_url))
    await apns.pool.__aenter__()
    return apns


from dataclasses import dataclass


@dataclass(frozen=True)
class APNS2:
    ssl_context: ssl.SSLContext
    server: config.Server

    async def __aenter__(self):
        # FIXME add explicit connect timeout,
        #   it's not the same as ssl handshake timeout
        # APN host name resolves to IPv4 only today...
        self.r, self.w = await asyncio.open_connection(
            host, port, ssl=ssl_context, ssl_handshake_timeout=5
        )
        info = self.w.get_extra_info("ssl_object")
        assert info, "HTTP/2 server is required"
        proto = indent.selected_alpn_protocol()
        assert proto == "h2", "Failed to negotiate HTTP/2"

    async def __aexit__(self, exc_type, exc, tb):
        # FIXME kill all requests in flight
        self.w.close()
        await self.w.wait_closed()

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
        url, headers, body = encode_request(
            token=token,
            notification=notification,
            apns_id=apns_id,
            expiration=expiration,
            priority=priority,
            topic=topic,
            collapse_id=collapse_id,
            server=self.server,
        )

        response = await self.foobar(url, headers, body)
        xx = await self.foobaz(response)
        return xx


async def create_client_h2(
    client_cert_path: str,
    server: config.Server,
    *,
    logger: Optional[BoundLogger] = None,
    timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
) -> APNS:
    ssl_context = ssl.create_default_context()
    ssl_context.options |= ssl.OP_NO_TLSv1
    ssl_context.options |= ssl.OP_NO_TLSv1_1
    ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_cert_path)
    return APNS2(ssl_context, server)
