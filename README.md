# AAPNS

[![CircleCI](https://circleci.com/gh/HENNGE/aapns.svg?style=svg)](https://circleci.com/gh/HENNGE/aapns)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.

## Raw H2

* Requires TLS 1.2 or better
* Requires Python 3.8 or better
* APN only runs on IPv4, don't worry about happy eyeballs

### Plan

#### Low level

A class that encapsulates a single HTTP/2 connection.

API:
* usage `post(header, body, deadline)` → ok or exception
* state `.closing` `.closed`
* back pressure `.stuck` — cannot presently send more to network
* back pressure callback TBD
* no priorities

#### Mid level

A class that encapsulates a connection pool.

* up to N active connections
* [maybe priorities - separate pool for each?]
* one? many? connections being started
* up to M connections shutting down
* a "small" queue for incoming user requests when connections are stuck
* usage `post(header, body, deadline)` → will post and retry

#### High level

TBD...

* easy context manager
* simple API to send notifications

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
