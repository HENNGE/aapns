from asyncio import (
    CancelledError,
    Event,
    TimeoutError,
    create_task,
    gather,
    sleep,
    wait_for,
)
from contextlib import suppress
from dataclasses import dataclass, field
from itertools import count
from logging import getLogger
from random import shuffle
from time import time
from typing import Set
from unittest.mock import patch

import h2.config
import h2.connection
import h2.settings
import yarl

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


class Pool:
    """Super-silly, fixed-size connection pool"""

    closing = closed = False
    base_url = None

    def __str__(self):
        alive = "\n".join(map(str, self.conn))
        dying = "\n".join(map(str, self.dying))
        return f"""<Pool
            alive:
            {alive}
            dying:
            {dying}>"""

    def __init__(self, base_url: str, size=10, ssl=None, logger=None):
        self.base_url = base_url
        self.conn: Set[Connection] = set()
        self.dying: Set[Connection] = set()
        self.ssl = ssl if ssl else create_ssl_context()
        assert size > 0
        self.size = size
        self._size_event = Event()
        self.logger = logger or getLogger("aapns")

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
                c = Connection(self.base_url, ssl=self.ssl)
                try:
                    await c.__aenter__()
                    self.conn.add(c)
                except Exception:
                    self.logger.exception("New connection failed")
                    break

            # FIXME wait for a trigger:
            # * resize
            # * some connection state has changed
            with suppress(TimeoutError):
                await wait_for(self._size_event.wait(), timeout=1)
            self._size_event.clear()

    async def __aenter__(self):
        self.bg = create_task(self.background_resize(), name="bg-resize")
        self.conn = set(
            Connection(self.base_url, ssl=self.ssl) for i in range(self.size)
        )
        await gather(*(c.__aenter__() for c in self.conn))
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

    async def post(self, req: "Request") -> "Response":
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

            await sleep(delay)
        else:
            raise Timeout()
