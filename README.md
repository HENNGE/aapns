# AAPNS

[![CircleCI](https://circleci.com/gh/HENNGE/aapns.svg?style=svg)](https://circleci.com/gh/HENNGE/aapns)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.

## Raw H2

* Requires TLS 1.2 or better
* Requires Python 3.8 or better
* APN only runs on IPv4, don't worry about happy eyeballs

### vs. httpx

Rationale for using raw https://github.com/python-hyper/hyper-h2 rather than an existing library, like https://github.com/encode/httpx.
Let's contrast the push notification use-case vs. generic, browser-like use-case.

| feature                   | push-like | browser-like                           |
|:--------------------------|:----------|:---------------------------------------|
| request size              | tiny      | small or large                         |
| request method            | `POST`    | `OPTIONS,HEAD,GET,PUT,POST`,custom     |
| response size             | tiny      | small, large, giant, streamed          |
| server push               | no        | possible                               |
| concurrent per connection | `1000`    | dozens                                 |
| total per connection      | millions  | dozens                                 |
| retryable                 | all       | idenpotent requests, graceful shutdown |
| servers                   | `1`       | many                                   |
| authorisation             | client cert or token | none, token, other          |

### Other

* Apple server sets max concurrent requests to `1000` and push requests are small, and max request size is 5KB, thus TCP send buffer will always be quite small, thus:
  * removed setting `TCP_NOTSENT_LOWAT`
  * removed `SO_NWRITE` check

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
