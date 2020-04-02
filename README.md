# AAPNS

[![CircleCI](https://circleci.com/gh/HENNGE/aapns.svg?style=svg)](https://circleci.com/gh/HENNGE/aapns)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.

* Requires TLS 1.2 or better
* Requires Python 3.8 or better

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

### On idempotency

Or how to ensure that a notification is displayed only once in the presence of network errors?

If the underlying TCP connection is broken or times out, there may be some notifications still in flight. In that case it's impossible to tell whether the notification was not yet delivered to the Apple server, or it was delivered but Apple server response was not yet delivered to back to your client.

Additionally, the server may try to shut down an HTTP/2 connection gracefully, for example for server maintenance or upgrade. In that case, due to https://github.com/python-hyper/hyper-h2/issues/1181, it is not possible to tell which of the in-flight requests will be completed by the server.

The Apple push notification protocol provides a header field `apns-collapse-id`. In the simple use-case, it's recommended to set this field, for example to a random value:

```py
await client.send_notification(
    ...,
    collapse_id=str(random.random()),
)
```

Then, should `aapns` need to retransmit the request, and retransmit "one time too many", the end user will only see a single notification.

However, if you are rely on notification collapse mechanism, if `aapns` retransmits, the notifications may arrive out of order, and the end-user may ultimately see stale data.

### Mid level API

Use this API to maintain a fixed size connection pool and gain automatic retries with exponential back-off. A connection pool can handle up to `1000 * size` (current Apple server limit) concurrent requests and practically unlimited dumb queue of requests should concurrency limit be exceeded. It is thus suitable for bursty traffic.

Use this API to send notification en masse or generic RPC-like communication over HTTP/2.

```py
from aapns.pool import create_ssl_context, Pool, Request
from aapns.pool import Closed, Timeout


ssl_context = create_ssl_context()
ssl_context.load_cert_chain(certfile=..., keyfile=...)

req = Request.new(
    "/3/device/42...42",
    {"apns-push-type": "alert", "apns-topic": "com.app.your"},
    {"apns": {"alert": {"body": "Wakey-wakey, ham and bakey!"}}},
    timeout=10,  # or the pool may retry forever
)

async with Pool(
        "https://api.development.push.apple.com",
        size=10,  # default
        ssl=ssl_context,
        logger=<optional>) as pool:
    try:
        resp = await pool.post(req)
        assert resp.code == 200
    except Timeout:
        ...  # the notification has expired
    except Closed:
        ...  # the connection pool is done, rare
```

### Low level API

Use this API if you want close control of a single connection to the server. A connection can handle up to `1000` concurrent requests (current Apple server limit) and up to `2**31` requests in total (HTTP/2 protocol limit).

This would be a good start for token authentication, https://github.com/HENNGE/aapns/issues/19.

```py
from aapns.connection import create_ssl_context, Connection, Request
from aapns.connection import Blocked, Closed, Timeout


ssl_context = create_ssl_context()
ssl_context.load_cert_chain(certfile=..., keyfile=...)

req = Request.new(
    "/3/device/42...42",
    {"apns-push-type": "alert", "apns-topic": "com.app.your"},
    {"apns": {"alert": {"body": "Wakey-wakey, ham and bakey!"}}},
    timeout=10)

async with Connection(
        "https://api.development.push.apple.com",
        ssl=ssl_context,
        logger=<optional>) as conn:
    try:
        resp = await conn.post(req)
        assert resp.code == 200
    except Blocked:
        ...  # the connection is busy, try again later
    except Closed:
        ...  # the connection is no longer usable
    except Timeout:
        ...  # the notification has expired
```

### Technical notes

Rationale for using raw https://github.com/python-hyper/hyper-h2 rather than an existing library, like https://github.com/encode/httpx.

Contrast push notification use-case vs. generic, browser-like use-case:

| feature                   | push-like | browser-like                        |
|:--------------------------|:----------|:------------------------------------|
| request size              | tiny      | small or large                      |
| request method            | `POST`    | `OPTIONS,HEAD,GET,PUT,POST`,custom  |
| response size             | tiny      | small, large, giant, streamed       |
| server push               | no        | possible                            |
| concurrent per connection | `1000`    | dozens                              |
| total per connection      | millions  | dozens                              |
| retryable                 | all       | idempotent verbs, graceful shutdown |
| servers                   | `1`       | many                                |
| authorisation             | client cert or token | none, token, other       |

* Apple server sets max concurrent requests to `1000` and push requests are small (5KB max), thus TCP send buffer will be quite small, thus:
  * we're not setting `TCP_NOTSENT_LOWAT`
  * we're not checking `SO_NWRITE/SIOCOUTQ`
* APN server is available on IPv4 only today, thus we don't worry about happy eyeballs
