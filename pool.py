import asyncio
import contextlib
import itertools
import json
import logging
import math
import random
import ssl
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import h2.config
import h2.connection
import h2.settings
import yarl

# FIXME `simple`
from simple import Request, Response
from simple import Connection
from simple import Blocked, Closed, Timeout, FormatError


class Pool:
    """Super-silly, fixed-size connection pool"""

    closing = closed = False
    base_url = None
    size = 10
    conn = None
    dying = None

    def __str__(self):
        alive = "\n".join(map(str, self.conn))
        dying = "\n".join(map(str, self.dying))
        return f"""<Pool
            alive:
            {alive}
            dying:
            {dying}>"""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.conn = set()
        self.dying = set()

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
                c = Connection(self.base_url)
                try:
                    await c.__aenter__()
                    self.conn.add(c)
                except Exception:
                    logging.exception("New connection failed")
                    break

            # FIXME a way to trigger resize
            await asyncio.sleep(1)

    async def __aenter__(self):
        self.bg = asyncio.create_task(self.background_resize())
        self.conn = [Connection(self.base_url) for i in range(self.size)]
        await asyncio.gather(*(c.__aenter__() for c in self.conn))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closing = True
        try:
            if self.bg:
                self.bg.cancel()
                await self.bg

            await asyncio.gather(
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
        random.shuffle(conns)
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
        for delay in (10 ** i for i in itertools.count(-3, 0.5)):
            if self.closing:
                raise Closed()

            try:
                return await self.post_once(req)
            except Blocked:
                pass

            if self.closing:
                raise Closed()

            if time.monotonic() + delay > req.deadline:
                raise Timeout()

            await asyncio.sleep(delay)


import collections
import pytest

stats = collections.defaultdict(int)

pytestmark = pytest.mark.asyncio


async def one_request(c, i):
    try:
        req = Request.new(
            f"https://localhost:2197/3/device/aaa-{i}",
            dict(foo="bar"),
            dict(baz=42),
            timeout=min(i * 0.1, 10),
        )
        resp = await c.post(req)
        # logging.info("%s %s %s", i, resp.code, resp.data)
        stats[resp.code] += 1
    except (Timeout, Blocked, Closed) as e:
        # logging.info("%s %r", i, e)
        stats[repr(e)] += 1


async def test_many(count=1000):
    c = None
    async def monitor():
        while True:
            logging.info("Pool %s", c)
            await asyncio.sleep(.1)

    mon = asyncio.create_task(monitor())

    try:
        async with Pool("https://localhost:2197") as c:
            # FIXME how come it's worse with the initial wait?
            await asyncio.sleep(0.1)
            await asyncio.gather(*[one_request(c, i) for i in range(-2, count, 2)])
    except Closed:
        logging.warning("Oops, closed")
    finally:
        print(stats)
        mon.cancel()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    asyncio.run(test_many(count))
