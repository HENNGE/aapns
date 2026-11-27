use std::fmt;

/// Enum defining the priority of the notification
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum Priority {
    Immediately = 10,
    #[default]
    Normal = 5,
}

impl Priority {
    pub const fn value(self) -> u8 {
        self as u8
    }
}

impl fmt::Display for Priority {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.value())
    }
}

pub const PRODUCTION_HOST: &str = "api.push.apple.com";
pub const SANDBOX_HOST: &str = "api.development.push.apple.com";
pub const DEFAULT_PORT: u16 = 443;
pub const ALT_PORT: u16 = 2197;
pub const MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP: usize = 5120;
pub const MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER: usize = 4096;
