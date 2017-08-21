import os
from asyncio import Protocol, Future, Transport
from enum import auto, Enum
from typing import Optional, Tuple, List, Dict, Union

import attr
from h2.connection import H2Connection
from h2.events import (
    ResponseReceived, DataReceived, StreamEnded,
    StreamReset,
)
from hyperframe.frame import SettingsFrame
from structlog import wrap_logger, PrintLogger, BoundLogger

from aapns.errors import Disconnected
from . import errors


SIZE = 4096


@attr.s
class PendingResponse:
    logger = attr.ib()
    future = attr.ib(default=attr.Factory(Future))
    headers = attr.ib(default=None)
    body = attr.ib(default=b'')

    def to_response(self):
        headers = {
            key.decode('utf-8'): value.decode('utf-8')
            for key, value in self.headers
        }
        status = int(headers[':status'])
        return Response(status, headers, self.body)


@attr.s
class Response:
    status: int = attr.ib()
    headers: Dict[str, str] = attr.ib()
    body: bytes = attr.ib()


class States(Enum):
    connecting = auto()
    connected = auto()
    disconnected = auto()


class APNSProtocol(Protocol):
    def __init__(self, authority: str, logger: Optional[BoundLogger]=None):
        self.authority = authority
        self.logger = logger or wrap_logger(PrintLogger(open(os.devnull, 'w')))
        self.conn = H2Connection()
        self.transport: Union[Transport, None] = None
        self.responses: Dict[int, PendingResponse] = {}
        self.state = States.connecting

    async def request(self,
                      headers: List[Tuple[str, str]],
                      body: bytes) -> Response:
        if self.state is States.disconnected:
            raise Disconnected()
        stream_id = self.conn.get_next_available_stream_id()
        logger = self.logger.bind(stream_id=stream_id)
        pending = self.responses[stream_id] = PendingResponse(logger=logger)
        logger.debug('request', headers=headers, body=body)
        self.conn.send_headers(stream_id, headers)
        self.conn.send_data(stream_id, body, end_stream=True)
        if self.transport is not None:
            data_to_send = self.conn.data_to_send()
            if data_to_send:
                self.transport.write(data_to_send)
        await pending.future
        return pending.to_response()

    async def close(self):
        self.conn.close_connection()
        if self.transport:
            self.transport.write(self.conn.data_to_send())
            self.transport.close()
        self.transport = None
        self.state = States.disconnected

    def connection_made(self, transport: Transport):
        self.logger.debug('connected')
        self.transport = transport
        self.conn.initiate_connection()

        # This reproduces the error in #396, by changing the header table size.
        self.conn.update_settings({SettingsFrame.HEADER_TABLE_SIZE: SIZE})

        self.transport.write(self.conn.data_to_send())
        self.state = States.connected

    def connection_lost(self, exc):
        self.logger.debug('disconnected')
        self.transport = None
        for pending in self.responses.values():
            pending.future.set_exception(Disconnected())
        self.responses = {}
        self.state = States.disconnected

    def data_received(self, data: bytes):
        events = self.conn.receive_data(data)

        for event in events:
            if isinstance(event, ResponseReceived):
                self.handle_response(event.headers, event.stream_id)
            elif isinstance(event, DataReceived):
                self.handle_data(event.data, event.stream_id)
            elif isinstance(event, StreamEnded):
                self.end_stream(event.stream_id)
            elif isinstance(event, StreamReset):
                self.reset_stream(event.stream_id)
            else:
                self.logger.debug('ignored', h2event=event)

        data = self.conn.data_to_send()
        if data:
            self.transport.write(data)

    def handle_response(self, response_headers: List[Tuple[bytes, bytes]], stream_id: int):
        if stream_id in self.responses:
            self.responses[stream_id].logger.debug(
                'response-headers',
                headers=response_headers
            )
            self.responses[stream_id].headers = response_headers
        else:
            self.logger.warning(
                'unexpected-response',
                stream_id=stream_id,
                headers=response_headers
            )

    def handle_data(self, data: bytes, stream_id: int):
        if stream_id in self.responses:
            self.responses[stream_id].logger.debug(
                'response-body',
                data=data
            )
            self.responses[stream_id].body += data
        else:
            self.logger.warning(
                'unexpected-data',
                stream_id=stream_id,
                data=data
            )

    def end_stream(self, stream_id: int):
        if stream_id in self.responses:
            response = self.responses.pop(stream_id)
            response.logger.debug('end-stream')
            response.future.set_result(True)
        else:
            self.logger.warning('unexpected-end-stream', stream_id=stream_id)

    def reset_stream(self, stream_id: int):
        if stream_id in self.responses:
            response = self.responses.pop(stream_id)
            response.logger.debug('reset-stream')
            response.future.set_exception(errors.StreamResetError())
        else:
            self.logger.warning('unexpected-reset-stream', stream_id=stream_id)
