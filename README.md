# AAPNS

Asynchronous Apple Push Notification Service client.


## Quickstart


```python
from aapns import connect, Notification, Alert, Production, Priority

async def send_hello_world():
    connection = await connect('/path/to/push/cert.pem', Production)
    apns_id = await connection.send_notification(
        'my-device-token',
        Notification(
            alert=Alert(
                body='Hello World!'
            ),
            badge=42
        ),
        priority=Priority.immediately
    )
    print(f'Sent push notification with ID {apns_id}')'
    await connection.close()
```
