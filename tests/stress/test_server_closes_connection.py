""" Observed connection outcomes, so far:
    * Closed('ErrorCodes.NO_ERROR')
    * Closed('[Errno 54] Connection reset by peer') 
    * Closed('Server closed the connection')
"""
import logging

import pytest

pytestmark = pytest.mark.asyncio


async def test_nothing(ok_server):
    1 / 0
