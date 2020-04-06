import logging
import sys
from asyncio import CancelledError, create_task, gather, run, sleep
from collections import defaultdict
from contextlib import suppress
from typing import Any, Dict

import pytest

from aapns.pool import Blocked, Closed, Pool, Request, Timeout, create_ssl_context

stats: Dict[Any, int] = defaultdict(int)

pytestmark = pytest.mark.asyncio


async def one_request(c, i):
    try:
        req = Request.new(
            f"/3/device/aaa-{i}",
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
            await sleep(0.1)

    mon = create_task(monitor())

    ssl_context = create_ssl_context()
    ssl_context.load_verify_locations(cafile="tests/stress/go1/cert.pem")
    ssl_context.load_cert_chain(certfile=".fake-cert", keyfile=".fake-cert")

    try:
        async with Pool("https://localhost:2197", ssl=ssl_context) as c:
            await sleep(0.1)
            await gather(*[one_request(c, i) for i in range(count)])
    except Closed:
        logging.warning("Oops, closed")
    finally:
        print(stats)
        mon.cancel()
        with suppress(CancelledError):
            await mon


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    run(test_many(count))
