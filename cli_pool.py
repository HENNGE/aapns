import asyncio
import collections
import logging

import pytest

from aapns.pool import Pool, Closed

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
