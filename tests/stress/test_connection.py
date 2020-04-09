""" Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer') 
    * Closed('Server closed the connection')
"""
import logging

import pytest

from aapns.connection import Connection

pytestmark = pytest.mark.asyncio


async def test_nothing(ok_server, ssl_context, request42):
    c = await Connection.create("https://localhost:2197", ssl_context)
    try:
        response = await c.post(request42)
        assert response
    finally:
        await c.close()
