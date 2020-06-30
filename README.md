# AAPNS

[![CircleCI](https://circleci.com/gh/HENNGE/aapns.svg?style=svg)](https://circleci.com/gh/HENNGE/aapns)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.

* Requires TLS 1.2 or better
* Requires Python 3.8 or better

## Quickstart

```python
from aapns.api import Server
from aapns.config import Priority
from aapns.models import Notification, Alert, Localized

async def send_hello_world():
    client = await Server.production('/path/to/push/cert.pem').create_client()
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
