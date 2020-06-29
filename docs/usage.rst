Usage
#####

.. highlight:: python

Creating a client
=================

A client is created from a :py:class:`aapns.api.Target`, which can be a
:py:class:`aapns.api.Server` when you want to interact with either production or development APNS, or a :py:class:`aapns.api.Simulator`
if you want to send notifications to a simulator running on your machine.
A :py:class:`aapns.api.Target` has an asynchronous method
:py:meth:`aapns.api.Target.create_client` which returns a client class.

Client instances are supposed to be long-lived. Ideally you create one
instance when your app starts up and call the coroutine
:py:meth:`aapns.api.APNSBaseClient.close` at the end of your apps life
cycle. If your target is a server, the client will internally use a
connection pool to allow higher throughput.

APNS Client
-----------

The easiest way to create a client to talk to APNS is to use one of the
provided classmethods on :py:class:`aapns.api.Server` to either connect
to production APNS or development APNS, using the standard or
alternative port, :py:meth:`aapns.api.Server.production`,
:py:meth:`aapns.api.Server.production_alt_port`,
:py:meth:`aapns.api.Server.development` or
:py:meth:`aapns.api.Server.development_alt_port`, and then calling
:py:meth:`aapns.api.Target.create_client` on the returned Target.

All four methods take a single argument, the path to the client certificate to use as a string.


Simulator Client
----------------

To talk to a local simulator, create an instance of
:py:class:`aapns.api.Simulator` providing the simulator device ID and
app ID as arguments, then call the :py:meth:`aapns.api.Target.create_client`
coroutine on that instance to create a client.


Sending Notifications
=====================

Once you have a client instance, you can send notifications by creating
instances of :py:class:`aapns.models.Notification` class.
In the most basic use case, pass it a :py:class:`aapns.models.Alert` object as
first argument. The :py:class:`aapns.models.Alert` requires at least a ``body``
to be given. See :doc:`api` for details about notifications and alerts.

Then call the coroutine :py:meth:`aapns.api.APNSBaseClient.send_notification`
on your client providing the push token for your device and the
notification instance as arguments. For simulators the push token can be
any string. For more control over sending notifications, see the
documentation for the other arguments in :doc:`api`.


Localization
============

The title and body of :py:class:`aapns.models.Alert` objects can be localized,
to do so use :py:class:`aapns.models.Localized` which takes the localization key
and an optional list of strings of localization arguments as arguments.


Putting it all together
=======================

Assuming we have our production certificate at ``/etc/apns.pem`` and the device
we try to send a notification to has the device token ``adevicetoken``, we could
send it a hello world notification like this::

    from aapns.api import Server
    from aapns.models import Notification, Alert

    async def send_hello_world():
        client = await Server.production('/etc/apns.pem').create_client()
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
