Usage
=====

.. highlight:: python

Creating a client
-----------------

Clients are created using the :py:func:`aapns.api.create_client` coroutine.

:py:func:`aapns.api.create_client` takes the path to your client certificate as first
argument, and a :py:class:`aapns.config.Server` object as second argument. You
should be able to simply use :py:obj:`aapns.config.Production` or
:py:obj:`aapns.config.Development` for the server argument depending on whether
you want to use the production or testing endpoints.


Sending Notifications
---------------------

Notifications are built using the :py:class:`aapns.models.Notification` class.
In the most basic use case, pass it a :py:class:`aapns.models.Alert` object as
first argument. The :py:class:`aapns.models.Alert` requires at least a ``body``
to be given. See :doc:`api` for details about notifications and alerts.

To send your notification, call the :py:meth:`aapns.api.APNS.send_notification`
coroutine on the client returned by :py:func:`aapns.api.create_client` coroutine.
:py:meth:`aapns.api.APNS.send_notification` takes a device token as string
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

    from aapns.api import create_client
    from aapns.config import Production
    from aapns.models import Notification, Alert

    async def send_hello_world():
        client = await create_client('/etc/apns.pem', Production)
        notification = Notification(
            alert=Alert(
                body='Hello World!'
            )
        )
        await client.send_notification('adevicetoken', notification)
        await client.close()


Command Line Client
-------------------

For testing, aapns also includes a small command line client to send notifications.
To use it, you must install it using ``pip install aapns[cli]``. See
``aapns --help`` for usage information.
