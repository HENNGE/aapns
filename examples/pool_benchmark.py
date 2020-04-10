"""Example of using aapns.pool.Pool (mid-level API)

Performance test for the connection pool.

Expects a local server on port 2197, for example, run:
    go run tests/stress/server-ok.go

Be careful if you target sandbox or production server, Apple won't like the flood.
Consider limiting number of requests to ~100 or so.
"""
import logging
import sys
from asyncio import CancelledError, create_task, gather, run, sleep
from collections import defaultdict
from contextlib import suppress
from typing import Any, Dict

from aapns.pool import Blocked, Closed, Pool, Request, Timeout, create_ssl_context


async def one_request(c, i):
    try:
        req = Request.new(
            f"/3/device/aaa-{i}",
            dict(foo="bar"),
            dict(baz=42),
            timeout=min(i * 0.1, 10),
        )
        resp = await c.post(req)
    except (Timeout, Blocked, Closed) as e:
        pass


async def many_requests(count):
    c = None

    async def monitor():
        try:
            while True:
                logging.info("Pool %s", c)
                await sleep(0.1)
        except:
            logging.info("Pool %s", c)
            raise

    mon = create_task(monitor())

    ssl_context = create_ssl_context()
    ssl_context.load_verify_locations(cafile=".test-server-certificate.pem")
    ssl_context.load_cert_chain(
        certfile=".test-client-certificate.pem", keyfile=".test-client-certificate.pem"
    )

    try:
        c = await Pool.create("https://localhost:2197", ssl=ssl_context)
        try:
            await sleep(0.1)
            await gather(*[one_request(c, i) for i in range(count)])
        finally:
            await c.close()
    except Closed:
        logging.warning("Oops, closed")
    finally:
        mon.cancel()
        with suppress(CancelledError):
            await mon


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    run(many_requests(count))
