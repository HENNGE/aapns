import json
from typing import *

import attr
from httpx import URL, AsyncClient, Response
from structlog import BoundLogger

from . import config, errors, models
from .pool import Pool, Request, create_ssl_context

try:
    # httpx 0.11.x
    from httpx.config import DEFAULT_TIMEOUT_CONFIG, TimeoutTypes
except ModuleNotFoundError:
    # httpx 0.12.x
    from httpx._config import DEFAULT_TIMEOUT_CONFIG, TimeoutTypes


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
    response_id = response.headers.get("apns-id", None)

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

        r = Request.new(
            path=f"/3/device/{token}",
            header={
                "apns-priority": str(priority.value),
                "apns-push-type": notification.push_type.value,
                **({"apns-id": apns_id} if apns_id else {}),
                **({"apns-expiration": str(expiration)} if expiration else {}),
                **({"apns-topic": topic} if topic else {}),
                **({"apns-collapse-id": collapse_id} if collapse_id else {}),
            },
            data=notification.get_dict(),
            timeout=10,
        )
        response_id_1 = (await self.pool.post(r)).apns_id

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

        response_id_2 = await handle_response(response)
        self.logger.warn(f"-> {response_id_1}, {response_id_2}")
        return response_id_1

    async def close(self):
        await self.client.aclose()
        await self.pool.__aexit__(None, None, None)


async def create_client(
    client_cert_path: str,
    server: config.Server,
    *,
    logger: Optional[BoundLogger] = None,
    timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
    cafile: str = None,
) -> APNS:
    client = AsyncClient(
        http2=True,
        cert=client_cert_path,
        timeout=timeout,
        **{"verify": cafile} if cafile else {},
    )
    base_url = f"https://{server.host}:{server.port}"
    ssl_context = create_ssl_context()
    if cafile:
        ssl_context.load_verify_locations(cafile=cafile)
    ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_cert_path)
    apns = APNS(client, logger, server, Pool(base_url, ssl=ssl_context))
    await apns.pool.__aenter__()
    return apns
