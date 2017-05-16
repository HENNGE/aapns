Usage
=====

.. highlight:: python

Connecting
----------

Connect to the APNS server using the :py:func:`aapns.connect` coroutine. To
close the connection, call the :py:meth:`aapns.connection.APNS.close` coroutine.

:py:func:`aapns.connect` takes the path to your client certificate as first
arugment, and a :py:class:`aapns.config.Server` object as second argument. You
should be able to simply use :py:obj:`aapns.config.Production` or
:py:obj:`aapns.config.Development` for the server argument depending on whether
you want to use the production or testing endpoints. You may pass a bound structlog
logger as a third, keyword only argument named ``logger`` to enable logging.


Sending Notifications
---------------------

Notifications are built using the :py:class:`aapns.models.Notification` class.
In the most basic use case, pass it a :py:class:`aapns.models.Alert` object as
first argument. The :py:class:`aapns.models.Alert` requires at least a ``body``
to be given. See :doc:`api` for details about notifications and alerts.

To send your notification, call the :py:meth:`aapns.connection.APNS.send_notification`
coroutine on the connection returned by :py:func:`aapns.connect` coroutine.
:py:meth:`aapns.connection.APNS.send_notification` takes a device token as string
as the first argument and a :py:class:`aapns.models.Notification` instance as second
argument. See :doc:`api` for details about the other arguments.


Localization
------------

The title and body of :py:class:`aapns.models.Alert` objects can be localized,
to do so use :py:class:`aapns.models.Localized` which takes the localization key
and an optional list of strings of localization arguments as arguments.


Putting it all together
-----------------------

Assuming we have our production certificate at ``/etc/apns.pem`` and the device
we try to send a notification to has the device token ``adevicetoken``, we could
send it a hello world notification like this::

    from aapns import connect, Notification, Alert, Production

    async def send_hello_world():
        connection = await connect('/etc/apns.pem', Production)
        notification = Notification(
            alert=Alert(
                body='Hello World!'
            )
        )
        await connection.send_notification('adevicetoken', notification)
        await connection.close()


Disconnects and timeouts
------------------------

If the connection disconnects, an :py:exc:`aapns.errors.Disconnected` exception
will be raised by :py:meth:`aapns.connection.APNS.send_notification` and you it
is your responsibility to deal with the consequences. A simple way to handle
disconnected connections would be to replace your connection with
:py:meth:`aapns.connection.APNS.reconect` (note that this returns a **new**
connection) and try sending the notification again.

aapns does not have a mechanism to detect timeouts built in. Instead, use
:py:func:`asyncio.wait_for`.


Command Line Client
-------------------

For testing, aapns also includes a small command line client to send notifications.
To use it, you must install it using ``pip install aapns[cli]``. See
``aapns --help`` for usage information.
