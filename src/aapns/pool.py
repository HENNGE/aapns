from asyncio import (
    CancelledError,
    Event,
    TimeoutError,
    create_task,
    gather,
    sleep,
    wait_for,
)
from contextlib import contextmanager, suppress
from itertools import count
from logging import getLogger
from random import shuffle
from time import time
from typing import Optional, Set

from .connection import Request, Response, create_ssl_context
from .errors import Blocked, Closed, FormatError, ResponseTooLarge, Timeout

logger = getLogger(__package__)


class Pool:
    """Super-silly, fixed-size connection pool"""

    started = closing = closed = False
    origin = None
    errors = retrying = completed = 0
    outcome: Optional[str] = None
    maintenance = None

    def __repr__(self):
        bits = [
            self.state,
            self.origin,
            f"alive:{len(self.conn)}",
            f"dying:{len(self.dying)}",
        ]
        if self.state != "closed":
            all = self.conn | self.dying
            bits.append(f"buffered:{sum(c.buffered for c in all)}")
            bits.append(f"inflight:{sum(c.inflight for c in all)}")
        bits.append(f"retrying:{self.retrying}")
        bits.append(f"completed:{self.completed}")
        bits.append(f"errors:{self.errors}")
        return "<Pool %s>" % " ".join(bits)

    @classmethod
    async def create(cls, origin: str, size=2, ssl=None):
        self = cls(origin, size, ssl)
        await self.__aenter__()
        return self

    def __init__(self, origin: str, size=2, ssl=None):
        """ Private, use `await Pool.create(...) instead. """
        self.origin = origin
        self.conn: Set[Connection] = set()
        self.dying: Set[Connection] = set()
        self.ssl = ssl if ssl else create_ssl_context()
        assert size > 0
        self.size = size
        self._size_event = Event()

    async def __aenter__(self):
        await gather(*(self.add_one_connection() for i in range(self.size)))
        self.maintenance = create_task(self.maintain(), name="maintenance")
        self.started = True
        return self

    @property
    def state(self):
        if not self.maintenance:
            return "new"
        elif not self.started:
            return "starting"
        elif not self.closing:
            return "active"
        elif not self.closed:
            return "closing"
        else:
            return "closed"

    def resize(self, size):
        assert size > 0
        self.size = size
        self._size_evet.set()

    def cert_hook(self, c):
        if not self.outcome and c.outcome == "BadCertificateEnvironment":
            self.closing = True
            self.outcome = c.outcome

    async def maintain(self):
        while True:
            if self.closing or self.closed:
                return

            for c in list(self.conn):
                if c.closing:
                    self.conn.remove(c)
                    self.dying.add(c)
                    self.cert_hook(c)

            while len(self.conn) > self.size:
                c = self.conn.pop()
                c.closing = True
                self.dying.add(c)
                self.cert_hook(c)

            for c in list(self.dying):
                if c.closed:
                    self.dying.remove(c)
                    self.cert_hook(c)
                elif not c.channels:
                    self.dying.remove(c)
                    try:
                        await c.__aexit__(None, None, None)
                    finally:
                        self.cert_hook(c)
                if self.closing or self.closed:
                    return

            while len(self.conn) < self.size:
                if not await self.add_one_connection():
                    break
                if self.closing or self.closed:
                    return

            # FIXME wait for a trigger:
            # * some connection state has changed
            with suppress(TimeoutError):
                await wait_for(self._size_event.wait(), timeout=1)
            self._size_event.clear()

    async def add_one_connection(self):
        try:
            c = await Connection.create(self.origin, ssl=self.ssl)
            self.conn.add(c)
            self.cert_hook(c)
            return True
        except Exception:
            logger.exception("Failed creating APN connection")

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def close(self):
        self.closing = True
        if not self.outcome:
            self.outcome = "Closed"
        try:
            if self.maintenance:
                self.maintenance.cancel()
                with suppress(CancelledError):
                    await self.maintenance

            await gather(*(c.close() for c in self.conn | self.dying))
        finally:
            self.closed = True

    async def post(self, req: "Request") -> "Response":
        with self.count_requests():
            for delay in (10 ** i for i in count(-3, 0.5)):
                if self.closing:
                    raise Closed(self.outcome)

                try:
                    return await self.post_once(req)
                except Blocked:
                    pass

                if self.closing:
                    raise Closed(self.outcome)

                if time() + delay > req.deadline:
                    raise Timeout()

                try:
                    self.retrying += 1
                    await sleep(delay)
                finally:
                    self.retrying -= 1

            raise Timeout()

    @contextmanager
    def count_requests(self):
        try:
            yield
        except:
            self.errors += 1
            raise
        else:
            self.completed += 1

    async def post_once(self, req: "Request") -> "Response":
        # FIXME ideally, follow weighted round-robin discipline:
        # * generally allocate requests evenly across connections
        # * but keep load for few last connections lighter
        #   to prevent all connections expiring at once
        # * ideally track connection backlog

        # FIXME handle connection getting closed
        # FIXME handle connection replacement
        conns = list(self.conn)
        shuffle(conns)
        for c in conns:
            if self.closing:
                raise Closed()
            if c.closed:
                continue
            try:
                return await c.post(req)
            except (Blocked, Closed):
                pass
        else:
            raise Blocked()
