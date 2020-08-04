from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from . import config, errors, models
from .config import (
    MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER,
    MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP,
)
from .models import PushType
from .pool import Pool, PoolProtocol, Request, create_ssl_context


class APNSBaseClient(metaclass=abc.ABCMeta):
    """
    Abstract base class defining the APIs a Client implementation must
    provide.
    """

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
        """
        Send the notification to the device identified by the token.
        For details about the parameters, refer to Appls APNS documentation.
        """
        pass

    async def close(self):
        """
        Close the client. This coroutine should only be called once and
        the client cannot be used anymore afterwards.
        """
        pass


class Target(metaclass=abc.ABCMeta):
    """
    Abstract base class defining the API a Target must implement.
    """

    @abc.abstractmethod
    async def create_client(self) -> APNSBaseClient:
        pass


@dataclass(frozen=True)
class Server(Target):
    """
    Target to talk to APNS Servers. Most use cases are covered by the convenience
    classmethods provided, so instantiating this class directly is rarely required.

    If you wish to create a custom instance of this class, you must provide the path
    to the client certificate to use as a string, the hostname of the server as a string,
    the port of hte server as an integer, and optionally the path to a root certificate to
    trust for TLS and the desired connection pool size.
    """

    client_cert_path: str
    host: str
    port: int = config.DEFAULT_PORT
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
        """
        Returns the server configured to connect to production APNS.
        """
        return cls(client_cert_path=client_cert_path, host=config.PRODUCTION_HOST)

    @classmethod
    def production_alt_port(cls, client_cert_path: str) -> Server:
        """
        Returns the server configured to connect to production APNS using the
        alternative port.
        """
        return replace(cls.production(client_cert_path), port=config.ALT_PORT)

    @classmethod
    def development(cls, client_cert_path: str) -> Server:
        """
        Returns the server configured to connect to development APNS.
        """
        return cls(client_cert_path=client_cert_path, host=config.SANDBOX_HOST)

    @classmethod
    def development_alt_port(cls, client_cert_path: str) -> Server:
        """
        Returns the server configured to connect to development APNS using the
        alternative port.
        """
        return replace(cls.development(client_cert_path), port=config.ALT_PORT)


@dataclass(frozen=True)
class Simulator(Target, APNSBaseClient):
    """
    Target to talk to a local simulator. Provide the device ID of your simulator
    and the app ID of your app as arguments.
    """

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
            path = Path(workspace) / "notification.apns"
            path.write_bytes(notification.encode())

            process = await asyncio.create_subprocess_exec(
                "xcrun", "simctl", "push", self.device_id, self.app_id, path,
            )
            await process.communicate()
            if process.returncode != 0:
                raise Exception("xcrun failed")
        return None


@dataclass(frozen=True)
class APNS(APNSBaseClient):
    pool: PoolProtocol

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

        request = Request.new(
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
        body_size = len(request.body)
        if notification.push_type is PushType.voip:
            if body_size > MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP:
                raise ValueError(
                    f"Notification payload exceeds maximum payload size for voip "
                    f"notifications of {MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP} bytes, "
                    f"it is {body_size} bytes"
                )
        elif body_size > MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER:
            raise ValueError(
                f"Notification payload exceeds maximum payload size for non-voip "
                f"notifications of {MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER} bytes, "
                f"it is {body_size} bytes"
            )

        response = await self.pool.post(request)
        if response.code != 200:
            raise errors.get(response.reason, response.apns_id)
        return response.apns_id

    async def close(self):
        await self.pool.close()
