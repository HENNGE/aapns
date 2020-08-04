from dataclasses import dataclass
from typing import Type

import pytest
from aapns.api import APNS
from aapns.connection import Request, Response
from aapns.models import Alert, Notification, PushType

pytestmark = [pytest.mark.asyncio]


@dataclass
class NullPool:
    exc_type: Type[Exception]

    async def post(self, request: Request) -> Response:
        raise self.exc_type()

    async def close(self):
        pass


# body becomes {"aps":{"alert":{"body":"<body>"}}} so there's a fixed 29 byte overhead
@pytest.mark.parametrize(
    "push_type,inner_body_size,allowed",
    [
        (PushType.alert, 4067, True),
        (PushType.alert, 4068, False),
        (PushType.alert, 5091, False),
        (PushType.alert, 5092, False),
        (PushType.background, 4067, True),
        (PushType.background, 4068, False),
        (PushType.background, 5091, False),
        (PushType.background, 5092, False),
        (PushType.voip, 4067, True),
        (PushType.voip, 4068, True),
        (PushType.voip, 5091, True),
        (PushType.voip, 5092, False),
        (PushType.complication, 4067, True),
        (PushType.complication, 4068, False),
        (PushType.complication, 5091, False),
        (PushType.complication, 5092, False),
        (PushType.fileprovider, 4067, True),
        (PushType.fileprovider, 4068, False),
        (PushType.fileprovider, 5091, False),
        (PushType.fileprovider, 5092, False),
        (PushType.mdm, 4067, True),
        (PushType.mdm, 4068, False),
        (PushType.mdm, 5091, False),
        (PushType.mdm, 5092, False),
    ],
)
async def test_request_too_large(push_type, inner_body_size, allowed):
    class Sentinel(Exception):
        pass

    api = APNS(NullPool(Sentinel))
    notification = Notification(
        alert=Alert(body="a" * inner_body_size), push_type=push_type
    )

    if allowed:
        context = pytest.raises(Sentinel)
    else:
        context = pytest.raises(ValueError)
    with context:
        await api.send_notification("token", notification)
