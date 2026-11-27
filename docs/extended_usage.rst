Extended Usage
==============

Quickstart
----------

.. code-block:: rust

   use aapns::api::{Client, SendOptions, Server, Target};
   use aapns::config::Priority;
   use aapns::models::{Alert, Localized, Notification};

   async fn send_hello_world() -> aapns::errors::Result<()> {
       let client = Server::production("/path/to/push/cert.pem").create_client().await?;
       let apns_id = client
           .send_notification(
               "my-device-token",
               &Notification {
                   badge: Some(42),
                   ..Notification::new(Alert::new(Localized::with_args(
                       "Hello World!",
                       ["foo", "bar"],
                   )))
               },
               &SendOptions {
                   priority: Priority::Immediately,
                   ..SendOptions::default()
               },
           )
           .await?;
       println!("Sent push notification with ID {apns_id:?}");
       client.close().await;
       Ok(())
   }

On idempotency
--------------

Or how to ensure that a notification is displayed only once in the presence of network errors?

If the underlying TCP connection is broken or times out, there may be some notifications still in flight. In that case it's impossible to tell whether the notification was not yet delivered to the Apple server, or it was delivered but Apple server response was not yet delivered to back to your client.

The Apple push notification protocol provides a header field ``apns-collapse-id``. In the simple use-case, it's recommended to set this field, for example to a random value:

.. code-block:: rust

   client
       .send_notification(
           ...,
           &SendOptions {
               collapse_id: Some(random_id()),
               ..SendOptions::default()
           },
       )
       .await?;

Then, should ``aapns`` need to retransmit the request, and retransmit "one time too many", the end user will only see a single notification.

However, if you are rely on notification collapse mechanism, if ``aapns`` retransmits, the notifications may arrive out of order, and the end-user may ultimately see stale data.

Mid level API
-------------

Use this API to maintain a fixed size connection pool and gain automatic retries with exponential back-off. A connection pool can handle up to ``1000 * size`` (current Apple server limit) concurrent requests and practically unlimited dumb queue of requests should concurrency limit be exceeded. It is thus suitable for bursty traffic.

Use this API to send notification en masse or generic RPC-like communication over HTTP/2.

.. code-block:: rust

   use aapns::connection::Request;
   use aapns::errors::Error;
   use aapns::pool::Pool;
   use aapns::tls::create_tls_config;

   let tls = create_tls_config(Some(Path::new(...)), None)?;

   let req = Request::new(
       "/3/device/42...42",
       &[("apns-push-type", "alert"), ("apns-topic", "com.app.your")],
       &json!({"aps": {"alert": {"body": "Wakey-wakey, ham and bakey!"}}}),
   )?
   .with_timeout(Some(Duration::from_secs(10))); // or the pool may retry forever

   let pool = Pool::connect("https://api.development.push.apple.com", 10, Some(tls)).await?;
   match pool.post(&req).await {
       Ok(resp) => assert_eq!(resp.code, 200),
       Err(Error::Timeout(_)) => {} // the notification has expired
       Err(Error::Closed(_)) => {} // the connection pool is done, e.g. if client certificate has expired
       Err(_) => {} // rare
   }
   pool.close().await;

Low level API
-------------

Use this API if you want close control of a single connection to the server. A connection can handle up to ``1000`` concurrent requests (current Apple server limit) and up to ``2**30`` requests in total (HTTP/2 protocol limit).

This would be a good start for token authentication, https://github.com/HENNGE/aapns/issues/19.

.. code-block:: rust

   use aapns::connection::{Connection, Request};
   use aapns::errors::Error;
   use aapns::tls::create_tls_config;

   let tls = create_tls_config(Some(Path::new(...)), None)?;

   let req = Request::new(
       "/3/device/42...42",
       &[("apns-push-type", "alert"), ("apns-topic", "com.app.your")],
       &json!({"aps": {"alert": {"body": "Wakey-wakey, ham and bakey!"}}}),
   )?
   .with_timeout(Some(Duration::from_secs(10)));

   let conn = Connection::connect("https://api.development.push.apple.com", Some(tls)).await?;
   match conn.post(&req).await {
       Ok(resp) => assert_eq!(resp.code, 200),
       Err(Error::Blocked) => {} // the connection is busy, try again later
       Err(Error::Closed(_)) => {} // the connection is no longer usable
       Err(Error::Timeout(_)) => {} // the notification has expired
       Err(_) => {} // rare
   }
   conn.close().await;

Technical notes
---------------

Rationale for using raw https://github.com/hyperium/h2 rather than an existing library, like https://github.com/hyperium/hyper.

Contrast push notification use-case vs. generic, browser-like use-case:

.. list-table::
   :header-rows: 1

   * - feature
     - push-like
     - browser-like
   * - request size
     - tiny
     - small or large
   * - request method
     - ``POST``
     - ``OPTIONS,HEAD,GET,PUT,POST``\ ,custom
   * - response size
     - tiny
     - small, large, giant, streamed
   * - server push
     - no
     - possible
   * - concurrent per connection
     - ``1000``
     - dozens
   * - total per connection
     - millions
     - dozens
   * - retryable
     - all
     - idempotent verbs, graceful shutdown
   * - servers
     - ``1``
     - many
   * - authorisation
     - client cert or token
     - none, token, other



* Apple server sets max concurrent requests to ``1000`` and push requests are small (5KB max), thus TCP send buffer will be quite small, thus:

  * we're not setting ``TCP_NOTSENT_LOWAT``
  * we're not checking ``SO_NWRITE/SIOCOUTQ``

* APN server is available on IPv4 only today, thus we don't worry about happy eyeballs
* TCP keepalive and, on Linux, ``TCP_USER_TIMEOUT`` detect connections dropped silently by a middlebox, e.g. NAT
