# AAPNS

[![workflow](https://github.com/HENNGE/aapns/actions/workflows/workflow.yml/badge.svg)](https://github.com/HENNGE/aapns/actions/workflows/workflow.yml)
[![Documentation Status](https://readthedocs.org/projects/aapns/badge/?version=latest)](http://aapns.readthedocs.io/en/latest/?badge=latest)

Asynchronous Apple Push Notification Service client.

* Requires TLS 1.2 or better
* Requires Rust 1.85 or better

## Quickstart

```rust
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
```
