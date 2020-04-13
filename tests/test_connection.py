import ssl

import pytest

from aapns.connection import Connection

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "origin",
    (
        "just.hostname.is.invalid",
        "http://localhost",
        "https://user@localhost:1234",
        "https://user:pass@localhost:1234",
        "https://localhost:1234/foo/bar",
        "https://localhost:1234?q=ax",
        "https://localhost:1234;p=ax",
        "https://localhost:1234#frag",
    ),
)
async def test_bad_origin(origin):
    with pytest.raises(ValueError):
        await Connection.create(origin)


async def test_bad_context():
    context = ssl.SSLContext()
    context.options = 0
    with pytest.raises(ValueError):
        await Connection.create("https://localhost:1234", context)
