# AAPNS

[![CircleCI](https://circleci.com/gh/HDE/aapns/tree/master.svg?style=svg)](https://circleci.com/gh/HDE/aapns/tree/master)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.


## Quickstart


```python
from aapns.api import create_client
from aapns.config import Priority, Production
from aapns.models import Notification, Alert, Localized

async def send_hello_world():
    client = await create_client('/path/to/push/cert.pem', Production)
    apns_id = await client.send_notification(
        'my-device-token',
        Notification(
            alert=Alert(
                body=Localized(
                    key='Hello World!',
                    args=['foo', 'bar']
                ),
            ),
            badge=42
        ),
        priority=Priority.immediately
    )
    print(f'Sent push notification with ID {apns_id}')
    await client.close()
```
