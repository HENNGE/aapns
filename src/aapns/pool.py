from __future__ import annotations

import asyncio
import ssl
from asyncio import CancelledError, TimeoutError, create_task, gather, sleep, wait_for
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from itertools import count
from logging import getLogger
from random import shuffle
from typing import Optional, Protocol, Set

from .connection import Connection, Request, Response, create_ssl_context
from .errors import Blocked, Closed, Timeout

logger = getLogger(__package__)


class PoolProtocol(Protocol):
    async def post(self, request: Request) -> Response:
        ...

    async def close(self):
        ...


@dataclass(eq=False)
class Pool:
    """Simple fixed-size connection pool with automatic replacement

    Example use:

        pool = Pool.create(...)
        try:
            await pool.post(request)
        finally:
            await pool.close()

    """

    origin: str
    size: int
    ssl_context: ssl.SSLContext
    active: Set[Connection]
    dying: Set[Connection] = field(default_factory=set)
    closing: bool = False
    closed: bool = False
    errors: int = 0
    retrying: int = 0
    completed: int = 0
    outcome: Optional[str] = None
    maintenance: asyncio.Task = field(init=False)
    maintenance_needed: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    async def create(cls, origin: str, size=2, ssl=None) -> Pool:
        """Connect to `origin` and return a connection pool"""
        if size < 1:
            raise ValueError("Connection pool size must be strictly positive")
        ssl_context = ssl or create_ssl_context()
        connections = set(
            await gather(
                *(Connection.create(origin, ssl=ssl_context) for i in range(size))
            )
        )
        # FIXME run the hook / ensure no connection is dead
        return cls(origin, size, ssl_context, connections)

    def __post_init__(self):
        self.maintenance = create_task(self.maintain(), name="maintenance")

    async def post(self, request: "Request") -> "Response":
        """Post the `request` on a connection in this pool, with retries"""
        with self.count_requests():
            for delay in (10 ** i for i in count(-3, 0.5)):
                if self.closing:
                    raise Closed(self.outcome)

                try:
                    return await self.post_once(request)
                except Blocked:
                    pass

                if self.closing:
                    raise Closed(self.outcome)

                if request.get_time_left_or_fail() < delay:
                    raise Timeout("Request would time out awaiting retry")

                try:
                    self.retrying += 1
                    await sleep(delay)
                finally:
                    self.retrying -= 1

            assert False, "unreachable"

    async def close(self):
        """Terminate the connection pool and free up the resources"""
        self.closing = True
        if not self.outcome:
            self.outcome = "Closed"
        try:
            if self.maintenance:
                self.maintenance.cancel()
                with suppress(CancelledError):
                    await self.maintenance

            await gather(
                *(connection.close() for connection in self.active | self.dying)
            )
        finally:
            self.closed = True

    def __repr__(self):
        bits = [
            self.state,
            self.origin,
            f"active:{len(self.active)}",
            f"dying:{len(self.dying)}",
        ]
        if self.state != "closed":
            bits.append(f"buffered:{self.buffered}")
            bits.append(f"inflight:{self.inflight}")
        bits.append(f"retrying:{self.retrying}")
        bits.append(f"completed:{self.completed}")
        bits.append(f"errors:{self.errors}")
        return "<Pool %s>" % " ".join(bits)

    def resize(self, size: int):
        """Resize the connection pool

        Give the pool some time to grow/shrink to new `size` afterwards.
        """
        if size < 1:
            raise ValueError("Connection pool size must be strictly positive")
        self.size = size
        self.maintenance_needed.set()

    @property
    def state(self):
        return "closed" if self.closed else "closing" if self.closing else "active"

    @property
    def inflight(self):
        """Count of the requests that were sent out and are awaiting server response."""
        return sum(c.inflight for c in self.active | self.dying)

    @property
    def buffered(self):
        """Count of the requests that we are still to send out."""
        return sum(c.buffered for c in self.active | self.dying)

    @property
    def pending(self):
        """Total count of pending requests."""
        return sum(c.pending for c in self.active | self.dying) + self.retrying

    def termination_hook(self, connection: Connection):
        """
        A hook to terminate the pool if/when client certificate expires.

        If Apple is not happy with our client certificate, it will close individual
        connections with a JSON blob with a specific message. All connections in the
        pool share same ssl context, and thus same client certificate. If one
        connection is closed so, then the entire pool is done for.
        """
        if not self.outcome and connection.outcome == "BadCertificateEnvironment":
            self.closing = True
            self.outcome = connection.outcome

    async def maintain(self):
        while not self.closing and not self.closed:
            for connection in list(self.active):
                if connection.closing:
                    self.active.remove(connection)
                    self.dying.add(connection)
                    self.termination_hook(connection)

            while len(self.active) > self.size:
                connection = self.active.pop()
                connection.closing = True
                self.dying.add(connection)
                self.termination_hook(connection)

            for connection in list(self.dying):
                if connection.closed:
                    self.dying.remove(connection)
                    self.termination_hook(connection)
                elif not connection.channels:
                    self.dying.remove(connection)
                    try:
                        await connection.close()
                    finally:
                        self.termination_hook(connection)
                if self.closing or self.closed:
                    return

            while len(self.active) < self.size:
                if not await self.add_one_connection():
                    break
                if self.closing or self.closed:
                    return

            # FIXME wait for a trigger:
            # * some connection state has changed
            with suppress(TimeoutError):
                await wait_for(self.maintenance_needed.wait(), timeout=1)
            self.maintenance_needed.clear()

    async def add_one_connection(self):
        try:
            connection = await Connection.create(self.origin, ssl=self.ssl_context)
            self.active.add(connection)
            self.termination_hook(connection)
            return True
        except OSError as e:
            logger.error("%s", e)
        except Exception:
            logger.exception("Failed creating APN connection")

    @contextmanager
    def count_requests(self):
        try:
            yield
        except:
            self.errors += 1
            raise
        else:
            self.completed += 1

    async def post_once(self, request: "Request") -> "Response":
        # FIXME ideally, follow weighted round-robin discipline:
        # * generally allocate requests evenly across connections
        # * but keep load for few last connections lighter
        #   to prevent all connections expiring at once
        # * ideally track connection backlog
        active = list(self.active)
        shuffle(active)
        for connection in active:
            if self.closing:
                raise Closed(self.outcome)
            if connection.closing or connection.closed:
                continue
            try:
                return await connection.post(request)
            except (Blocked, Closed):
                pass
        else:
            raise Blocked()
