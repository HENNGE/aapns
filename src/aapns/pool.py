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
from typing import Set

from .connection import (
    Blocked,
    Closed,
    Connection,
    FormatError,
    Request,
    Response,
    Timeout,
    create_ssl_context,
)

logger = getLogger(__package__)


class Pool:
    """Super-silly, fixed-size connection pool"""

    started = closing = closed = False
    origin = None
    errors = retrying = completed = 0

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

    def __init__(self, origin: str, size=10, ssl=None):
        self.origin = origin
        self.conn: Set[Connection] = set()
        self.dying: Set[Connection] = set()
        self.ssl = ssl if ssl else create_ssl_context()
        assert size > 0
        self.size = size
        self._size_event = Event()

    @property
    def state(self):
        if not self.bg:
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

    async def background_resize(self):
        while True:
            if self.closing or self.closed:
                return

            for c in list(self.conn):
                if c.closing:
                    self.conn.remove(c)
                    self.dying.add(c)

            while len(self.conn) > self.size:
                c = self.conn.pop()
                c.closing = True
                self.dying.add(c)

            for c in list(self.dying):
                if c.closed:
                    self.dying.remove(c)
                elif not c.channels:
                    self.dying.remove(c)
                    await c.__aexit__(None, None, None)

            while len(self.conn) < self.size:
                c = Connection(self.origin, ssl=self.ssl)
                try:
                    await c.__aenter__()
                    self.conn.add(c)
                except Exception:
                    logger.exception("New connection failed")
                    break

            # FIXME wait for a trigger:
            # * resize
            # * some connection state has changed
            with suppress(TimeoutError):
                await wait_for(self._size_event.wait(), timeout=1)
            self._size_event.clear()

    async def __aenter__(self):
        self.bg = create_task(self.background_resize(), name="bg-resize")
        self.conn = set(Connection(self.origin, ssl=self.ssl) for i in range(self.size))
        await gather(*(c.__aenter__() for c in self.conn))
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closing = True
        try:
            if self.bg:
                self.bg.cancel()
                with suppress(CancelledError):
                    await self.bg

            await gather(
                *(c.__aexit__(exc_type, exc, tb) for c in self.conn),
                *(c.__aexit__(exc_type, exc, tb) for c in self.dying),
            )
        finally:
            self.closed = True

    async def post(self, req: "Request") -> "Response":
        with self.count_requests():
            for delay in (10 ** i for i in count(-3, 0.5)):
                if self.closing:
                    raise Closed()

                try:
                    return await self.post_once(req)
                except Blocked:
                    pass

                if self.closing:
                    raise Closed()

                if time() + delay > req.deadline:
                    raise Timeout()

                try:
                    self.retrying += 1
                    await sleep(delay)
                finally:
                    self.retrying -= 1
            else:
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
