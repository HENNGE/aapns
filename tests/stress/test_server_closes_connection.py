""" Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer') 
    * Closed('Server closed the connection')
"""
from os import killpg
from signal import SIGTERM
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
from contextlib import suppress
import logging


async def rr(what, name):
    with suppress(CancelledError):
        async for line in what:
            logging.warning("%s: %s", name, line.decode("utf-8"))


async def test_1():
    server = await create_subprocess_exec(
        "go", "run", "tests/stress/server-ok.go", stdout=PIPE, stderr=PIPE, start_new_session=True
    )
    logging.warning("server %r", server)
    try:
        to = asyncio.create_task(rr(server.stdout, "server:stdout"))
        te = asyncio.create_task(rr(server.stderr, "server:stderr"))
        try:
            await asyncio.sleep(5)
        finally:
            to.cancel()
            te.cancel()
    finally:
        with suppress(ProcessLookupError):
            # server.terminate() is not enough,`go run`'s child somehow survives
            # Thus, I'm starting the server in a new session and kill the entire session
            killpg(server.pid, SIGTERM)


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_1())
