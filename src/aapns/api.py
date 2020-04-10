from __future__ import annotations

import abc
import asyncio
import os
import shutil
from dataclasses import dataclass, replace
from tempfile import TemporaryDirectory
from typing import Optional

from . import config, errors, models
from .pool import Pool, Request, create_ssl_context


@dataclass(frozen=True)
class APNSBaseClient(metaclass=abc.ABCMeta):
    @abc.abstractmethod
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
        pass

    async def close(self):
        pass


@dataclass(frozen=True)
class Target(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def create_client(self) -> APNSBaseClient:
        pass


@dataclass(frozen=True)
class Server(Target):
    client_cert_path: str
    host: str
    port: int = 443
    ca_file: Optional[str] = None
    pool_size: int = 2

    async def create_client(self) -> APNSBaseClient:
        base_url = f"https://{self.host}:{self.port}"
        ssl_context = create_ssl_context()
        if self.ca_file:
            ssl_context.load_verify_locations(cafile=self.ca_file)
        ssl_context.load_cert_chain(
            certfile=self.client_cert_path, keyfile=self.client_cert_path
        )
        return APNS(await Pool.create(base_url, size=self.pool_size, ssl=ssl_context))

    @classmethod
    def production(cls, client_cert_path: str) -> Server:
        return cls(client_cert_path=client_cert_path, host="api.push.apple.com")

    @classmethod
    def production_alt_port(cls, client_cert_path: str) -> Server:
        return replace(cls.production(client_cert_path), port=2197)

    @classmethod
    def development(cls, client_cert_path: str) -> Server:
        return cls(client_cert_path=client_cert_path, host="api.development.apple.com")

    @classmethod
    def development_alt_port(cls, client_cert_path: str) -> Server:
        return replace(cls.production(client_cert_path), port=2197)


@dataclass(frozen=True)
class Simulator(Target, APNSBaseClient):
    device_id: str
    app_id: str

    async def create_client(self) -> APNSBaseClient:
        return self

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
        with TemporaryDirectory() as workspace:
            path = os.path.join(workspace, "notification.apns")
            with open(path, "wb") as fobj:
                fobj.write(notification.encode())

            process = await asyncio.create_subprocess_exec(
                shutil.which("xcrun"),
                "simctl",
                "push",
                self.device_id,
                self.app_id,
                path,
            )
            await process.communicate()
            if process.returncode != 0:
                raise Exception("xcrun failed")
        return None


@dataclass(frozen=True)
class APNS(APNSBaseClient):
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
