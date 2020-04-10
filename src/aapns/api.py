from typing import Optional

import attr

from . import config, errors, models
from .pool import Pool, Request, create_ssl_context


@attr.s(auto_attribs=True, frozen=True)
class APNS:
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
    ) -> Optional[str]:

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
        response = await self.pool.post(r)
        if response.code != 200:
            raise errors.get(response.reason, response.apns_id)
        return response.apns_id

    async def close(self):
        await self.pool.close()


async def create_client(
    client_cert_path: str, server: config.Server, *, cafile: Optional[str] = None,
) -> APNS:
    base_url = f"https://{server.host}:{server.port}"
    ssl_context = create_ssl_context()
    if cafile:
        ssl_context.load_verify_locations(cafile=cafile)
    ssl_context.load_cert_chain(certfile=client_cert_path, keyfile=client_cert_path)
    return APNS(server, await Pool.create(base_url, ssl=ssl_context))
