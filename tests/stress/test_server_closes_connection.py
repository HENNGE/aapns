""" Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer') 
    * Closed('Server closed the connection')
"""
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
import logging

async def test_1():
    x = await create_subprocess_exec("go", "run", "tests/stress/server-ok.go", stdout=PIPE, stderr=PIPE)
    w = logging.warning
    w("%s", x)
    w("%s", x.stdout)

    async def rr(what, name):
        async for line in what:
            w("%s: %s", name, line.decode("utf-8"))

    to = asyncio.create_task(rr(x.stdout, "server:stdout"))
    to = asyncio.create_task(rr(x.stderr, "server:stderr"))

    await asyncio.sleep(5)


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_1())
