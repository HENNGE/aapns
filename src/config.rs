//! Constants describing the Apple Push Notification service.

use std::fmt;

/// Priority of a notification, sent as the `apns-priority` header.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum Priority {
    /// Send the notification immediately.
    Immediately = 10,
    /// Send the notification based on power considerations on the device.
    #[default]
    Normal = 5,
}

impl Priority {
    /// The numeric value sent to APNs.
    pub const fn value(self) -> u8 {
        self as u8
    }
}

impl fmt::Display for Priority {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.value())
    }
}

/// Host name of the production APNs environment.
pub const PRODUCTION_HOST: &str = "api.push.apple.com";
/// Host name of the development (sandbox) APNs environment.
pub const SANDBOX_HOST: &str = "api.development.push.apple.com";
/// Default APNs port.
pub const DEFAULT_PORT: u16 = 443;
/// Alternative APNs port, for networks that block outgoing traffic on 443.
pub const ALT_PORT: u16 = 2197;
/// Maximum payload size of a VoIP notification, in bytes.
pub const MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP: usize = 5120;
/// Maximum payload size of any other notification, in bytes.
pub const MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER: usize = 4096;
