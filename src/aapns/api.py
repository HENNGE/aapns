import json
import ssl
from asyncio import get_event_loop, wait_for, Lock, sleep, BaseEventLoop
from typing import Optional

from structlog import BoundLogger

from . import errors, config, models, connection



class APNS:
    def __init__(self, client_cert_path, server, logger, ssl_context, *, auto_reconnect=False, timeout=None, loop=None):
        self.client_cert_path = client_cert_path
        self.server = server
        self.logger = logger
        self.ssl_context = ssl_context
        self.auto_reconnect = auto_reconnect
        self.run_coroutine = self._run_with_timeout if timeout is None else self._run_with_timeout
        self.timeout = timeout
        self.connection = None
        self.connection_lock = Lock()
        self.loop = loop or get_event_loop()

    async def send_notification(self,
                                token: str,
                                notification: models.Notification,
                                *,
                                apns_id: Optional[str]=None,
                                expiration: Optional[int]=None,
                                priority: config.Priority=config.Priority.normal,
                                topic: Optional[str]=None,
                                collapse_id: Optional[str]=None) -> str:
        conn = await self.run_coroutine(self.get_connection())

        request_body = notification.encode()
        request_headers = [
            (':method', 'POST'),
            (':authority', self.server.host),
            (':scheme', 'https'),
            (':path', f'/3/device/{token}'),
            ('content-length', str(len(request_body))),
            ('apns-priority', str(priority.value)),
        ]

        if apns_id:
            request_headers.append(('apns-id', apns_id))
        if expiration:
            request_headers.append(('apns-expiration', str(expiration)))
        if topic:
            request_headers.append(('apns-topic', topic))
        if collapse_id:
            request_headers.append(('apns-collapse-id', collapse_id))

        response = await self.run_coroutine(conn.request(
            headers=request_headers,
            body=request_body,
        ))

        response_id = response.headers.get('apns-id', '')

        if response.status != 200:
            try:
                reason = json.loads(response.body)['reason']
            except:
                reason = response.body
            exc = errors.get(reason, response_id)
            raise exc
        else:
            return response_id

    @classmethod
    async def init_with_connection(cls, client_cert_path, server, logger, ssl_context, *, auto_reconnect=False, timeout=None, loop=None):
        apns = cls(client_cert_path, server, logger, ssl_context, auto_reconnect=auto_reconnect, timeout=timeout, loop=loop)
        await apns.run_coroutine(apns.connect())
        return apns

    async def close(self):
        if self.connected:
            await self.connection.close()

    @property
    def connected(self):
        return (
            self.connection is not None
            and
            self.connection.state is not connection.States.disconnected
        )

    async def _run_with_timeout(self, coro):
        return await wait_for(coro, self.timeout, loop=self.loop)

    @staticmethod
    async def _run_without_timeout(coro):
        return await coro

    async def connect(self):
        self.connection = connection.APNSProtocol(self.server.host, self.logger)
        await self.loop.create_connection(
            lambda: self.connection,
            self.server.host,
            self.server.port,
            ssl=self.ssl_context
        )

    async def get_connection(self) -> connection.APNSProtocol:
        async with self.connection_lock:
            if not self.connected:
                if not self.auto_reconnect:
                    raise errors.Disconnected()
                while True:
                    try:
                        await self.connect()
                        break
                    except ConnectionRefusedError:
                        await sleep(0.1)
            return self.connection


async def connect(client_cert_path: str,
                  server: config.Server,
                  *,
                  ssl_context: Optional[ssl.SSLContext]=None,
                  logger: Optional[BoundLogger]=None,
                  auto_reconnect: bool=False,
                  timeout: Optional[int]=None,
                  loop: Optional[BaseEventLoop]=None) -> APNS:
    if ssl_context is None:
        ssl_context: ssl.SSLContext = ssl.create_default_context()
        ssl_context.set_alpn_protocols(['h2'])
        ssl_context.set_npn_protocols(['h2'])
    ssl_context.load_cert_chain(client_cert_path)
    return await APNS.init_with_connection(
        client_cert_path=client_cert_path,
        server=server,
        ssl_context=ssl_context,
        logger=logger,
        auto_reconnect=auto_reconnect,
        timeout=timeout,
        loop=loop
    )
