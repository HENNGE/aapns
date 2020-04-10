import ssl

import pytest

from aapns.pool import Pool

pytestmark = pytest.mark.asyncio


async def test_bad_pool_size():
    with pytest.raises(ValueError):
        await Pool.create("https://localhost:1234", 0)


async def test_bad_origin():
    with pytest.raises(ValueError):
        await Pool.create("just.host.name.is.invalid")


async def test_bad_context():
    context = ssl.SSLContext()
    context.options = 0
    with pytest.raises(ValueError):
        await Pool.create("https://localhost:1234", 2, context)
