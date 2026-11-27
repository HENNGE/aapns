Usage
#####

.. highlight:: rust

Creating a client
=================

A client is created from a ``aapns::api::Target``, which can be a
``aapns::api::Server`` when you want to interact with either production or development APNS, or a ``aapns::api::Simulator``
if you want to send notifications to a simulator running on your machine.
A ``Target`` has an asynchronous method
``create_client`` which returns a client.

Client instances are supposed to be long-lived. Ideally you create one
instance when your app starts up and call
``Client::close`` at the end of your apps life
cycle. If your target is a server, the client will internally use a
connection pool to allow higher throughput.

APNS Client
-----------

The easiest way to create a client to talk to APNS is to use one of the
provided constructors on ``Server`` to either connect
to production APNS or development APNS, using the standard or
alternative port, ``Server::production``,
``Server::production_alt_port``,
``Server::development`` or
``Server::development_alt_port``, and then calling
``create_client`` on the returned target.

All four take a single argument, the path to the client certificate to use.


Simulator Client
----------------

To talk to a local simulator, create a
``Simulator`` providing the simulator device ID and
app ID as arguments, then call ``create_client``
on it to create a client.


Sending Notifications
=====================

Once you have a client instance, you can send notifications by creating
instances of ``aapns::models::Notification``.
In the most basic use case, pass it a ``aapns::models::Alert``.
The ``Alert`` requires at least a ``body``
to be given. See :doc:`api` for details about notifications and alerts.

Then call ``send_notification``
on your client providing the push token for your device, the
notification and ``SendOptions``. For simulators the push token can be
any string. For more control over sending notifications, see the
documentation for ``SendOptions`` in :doc:`api`.


Localization
============

The title and body of ``Alert`` can be localized,
to do so use ``aapns::models::Localized`` which takes the localization key
and an optional list of strings of localization arguments.


Putting it all together
=======================

Assuming we have our production certificate at ``/etc/apns.pem`` and the device
we try to send a notification to has the device token ``adevicetoken``, we could
send it a hello world notification like this::

    use aapns::api::{Client, SendOptions, Server, Target};
    use aapns::models::{Alert, Notification};

    async fn send_hello_world() -> aapns::errors::Result<()> {
        let client = Server::production("/etc/apns.pem").create_client().await?;
        let notification = Notification::new(Alert::new("Hello World!"));
        client
            .send_notification("adevicetoken", &notification, &SendOptions::default())
            .await?;
        client.close().await;
        Ok(())
    }


Command Line Client
-------------------

For testing, aapns also includes a small command line client to send notifications.
To use it, you must install it using ``cargo install aapns --features cli``. See
``aapns --help`` for usage information.
