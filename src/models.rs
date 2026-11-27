//! Notification payload models.
//!
//! Field types are enforced at compile time, so invalid notifications cannot
//! be constructed. For example, localization arguments must be strings:
//!
//! ```compile_fail
//! # use aapns::models::Localized;
//! let localized = Localized::with_args("key", [1, 2, 3]);
//! ```
//!
//! and an alert always has a body:
//!
//! ```compile_fail
//! # use aapns::models::Alert;
//! let alert = Alert::new();
//! ```

use std::fmt;

use serde_json::{Map, Value};

/// A localized string, used for the body, title or subtitle of an [`Alert`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Localized {
    /// Key of the localized string in the app's `Localizable.strings`.
    pub key: String,
    /// Arguments substituted into the localized string. Omitted if empty.
    pub args: Vec<String>,
}

impl Localized {
    /// A localized string without arguments.
    pub fn new(key: impl Into<String>) -> Self {
        Localized {
            key: key.into(),
            args: Vec::new(),
        }
    }

    /// A localized string with arguments.
    pub fn with_args<I, S>(key: impl Into<String>, args: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Localized {
            key: key.into(),
            args: args.into_iter().map(Into::into).collect(),
        }
    }
}

/// Alert text, either verbatim or localized on the device.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Text {
    /// Text displayed as is.
    Plain(String),
    /// Text looked up in the app's localization files.
    Localized(Localized),
}

impl Text {
    fn write_to(&self, alert: &mut Map<String, Value>, plain: &str, key: &str, args: &str) {
        match self {
            Text::Plain(text) => {
                alert.insert(plain.into(), text.as_str().into());
            }
            Text::Localized(localized) => {
                alert.insert(key.into(), localized.key.as_str().into());
                if !localized.args.is_empty() {
                    alert.insert(args.into(), localized.args.clone().into());
                }
            }
        }
    }
}

impl From<&str> for Text {
    fn from(text: &str) -> Self {
        Text::Plain(text.to_owned())
    }
}

impl From<String> for Text {
    fn from(text: String) -> Self {
        Text::Plain(text)
    }
}

impl From<&String> for Text {
    fn from(text: &String) -> Self {
        Text::Plain(text.clone())
    }
}

impl From<Localized> for Text {
    fn from(localized: Localized) -> Self {
        Text::Localized(localized)
    }
}

/// The alert of a [`Notification`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Alert {
    /// Body of the alert.
    pub body: Text,
    /// Title of the alert.
    pub title: Option<Text>,
    /// Subtitle of the alert.
    pub subtitle: Option<Text>,
    /// Localization key of the action button.
    pub action_loc_key: Option<String>,
    /// Launch image to display.
    pub launch_image: Option<String>,
}

impl Alert {
    /// An alert with only a body.
    pub fn new(body: impl Into<Text>) -> Self {
        Alert {
            body: body.into(),
            title: None,
            subtitle: None,
            action_loc_key: None,
            launch_image: None,
        }
    }

    /// Sets the title.
    pub fn with_title(mut self, title: impl Into<Text>) -> Self {
        self.title = Some(title.into());
        self
    }

    /// Sets the subtitle.
    pub fn with_subtitle(mut self, subtitle: impl Into<Text>) -> Self {
        self.subtitle = Some(subtitle.into());
        self
    }

    /// The `alert` dictionary of the APNs payload.
    pub fn to_json(&self) -> Map<String, Value> {
        let mut alert = Map::new();
        if let Some(title) = &self.title {
            title.write_to(&mut alert, "title", "title-loc-key", "title-loc-args");
        }
        if let Some(subtitle) = &self.subtitle {
            subtitle.write_to(
                &mut alert,
                "subtitle",
                "subtitle-loc-key",
                "subtitle-loc-args",
            );
        }
        self.body
            .write_to(&mut alert, "body", "loc-key", "loc-args");
        if let Some(action_loc_key) = &self.action_loc_key {
            alert.insert("action-loc-key".into(), action_loc_key.as_str().into());
        }
        if let Some(launch_image) = &self.launch_image {
            alert.insert("launch-image".into(), launch_image.as_str().into());
        }
        alert
    }
}

/// Type of a push notification, sent as the `apns-push-type` header.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum PushType {
    /// A notification that displays an alert, plays a sound or badges the app icon.
    #[default]
    Alert,
    /// A notification that delivers content in the background.
    Background,
    /// A VoIP notification.
    Voip,
    /// A notification for a watchOS complication.
    Complication,
    /// A notification for a File Provider extension.
    FileProvider,
    /// A notification telling managed devices to contact the MDM server.
    Mdm,
}

impl PushType {
    /// The value sent to APNs.
    pub const fn as_str(self) -> &'static str {
        match self {
            PushType::Alert => "alert",
            PushType::Background => "background",
            PushType::Voip => "voip",
            PushType::Complication => "complication",
            PushType::FileProvider => "fileprovider",
            PushType::Mdm => "mdm",
        }
    }

    /// Maximum payload size APNs accepts for this push type, in bytes.
    pub const fn max_payload_size(self) -> usize {
        match self {
            PushType::Voip => crate::config::MAX_NOTIFICATION_PAYLOAD_SIZE_VOIP,
            _ => crate::config::MAX_NOTIFICATION_PAYLOAD_SIZE_OTHER,
        }
    }
}

impl fmt::Display for PushType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A notification to send. Refer to Apple's documentation for the meaning of
/// each field.
///
/// ```
/// use aapns::models::{Alert, Localized, Notification};
///
/// let notification = Notification {
///     badge: Some(42),
///     sound: Some("default".into()),
///     ..Notification::new(Alert::new(Localized::with_args("GREETING", ["Jane"])))
/// };
/// ```
#[derive(Debug, Clone, PartialEq)]
pub struct Notification {
    /// The alert to display.
    pub alert: Alert,
    /// Type of the notification.
    pub push_type: PushType,
    /// Number to display on the app icon; `Some(0)` removes the badge.
    pub badge: Option<u32>,
    /// Name of the sound to play.
    pub sound: Option<String>,
    /// Sets `content-available` to wake the app in the background.
    pub content_available: bool,
    /// Notification category, for actionable notifications.
    pub category: Option<String>,
    /// Identifier used to group notifications.
    pub thread_id: Option<String>,
    /// Sets `mutable-content` so that a notification service extension can
    /// modify the notification.
    pub mutable_content: bool,
    /// Identifier of the window brought forward when the notification is opened.
    pub target_content_id: Option<String>,
    /// Custom top-level keys added to the payload, next to `aps`.
    pub extra: Map<String, Value>,
}

impl Notification {
    /// A notification of type [`PushType::Alert`] with no optional fields set.
    pub fn new(alert: Alert) -> Self {
        Notification {
            alert,
            push_type: PushType::default(),
            badge: None,
            sound: None,
            content_available: false,
            category: None,
            thread_id: None,
            mutable_content: false,
            target_content_id: None,
            extra: Map::new(),
        }
    }

    /// The JSON payload of the notification.
    pub fn to_json(&self) -> Value {
        let mut aps = Map::new();
        aps.insert("alert".into(), self.alert.to_json().into());
        if let Some(badge) = self.badge {
            aps.insert("badge".into(), badge.into());
        }
        if let Some(sound) = &self.sound {
            aps.insert("sound".into(), sound.as_str().into());
        }
        if self.content_available {
            aps.insert("content-available".into(), 1.into());
        }
        if let Some(category) = &self.category {
            aps.insert("category".into(), category.as_str().into());
        }
        if let Some(thread_id) = &self.thread_id {
            aps.insert("thread-id".into(), thread_id.as_str().into());
        }
        if self.mutable_content {
            aps.insert("mutable-content".into(), 1.into());
        }
        if let Some(target_content_id) = &self.target_content_id {
            aps.insert(
                "target-content-id".into(),
                target_content_id.as_str().into(),
            );
        }
        let mut payload = Map::new();
        payload.insert("aps".into(), aps.into());
        payload.extend(self.extra.clone());
        payload.into()
    }

    /// The JSON payload of the notification, encoded as compact UTF-8 JSON.
    pub fn encode(&self) -> Vec<u8> {
        encode_json(&self.to_json())
    }
}

/// Compact JSON with keys in sorted order and non-ASCII characters as UTF-8.
pub(crate) fn encode_json(value: &Value) -> Vec<u8> {
    // Serializing a `Value` cannot fail: all map keys are strings.
    serde_json::to_vec(value).expect("JSON values always serialize")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn encode() {
        let notification = Notification::new(Alert::new("Content").with_title("Test"));
        assert_eq!(
            notification.encode(),
            br#"{"aps":{"alert":{"body":"Content","title":"Test"}}}"#
        );
    }

    #[test]
    fn encode_keeps_non_ascii() {
        let notification = Notification::new(Alert::new("こんにちは"));
        assert_eq!(
            String::from_utf8(notification.encode()).unwrap(),
            r#"{"aps":{"alert":{"body":"こんにちは"}}}"#
        );
    }

    #[test]
    fn localize() {
        let notification = Notification::new(
            Alert::new(Localized::new("Content"))
                .with_title(Localized::with_args("Test", ["foo", "bar"])),
        );
        assert_eq!(
            notification.to_json(),
            json!({
                "aps": {
                    "alert": {
                        "title-loc-key": "Test",
                        "title-loc-args": ["foo", "bar"],
                        "loc-key": "Content",
                    }
                }
            })
        );
    }

    #[test]
    fn subtitle() {
        let alert = Alert::new("body").with_subtitle("plain").to_json();
        assert_eq!(alert["subtitle"], "plain");
        let alert = Alert::new("body")
            .with_subtitle(Localized::with_args("SUB", ["x"]))
            .to_json();
        assert_eq!(alert["subtitle-loc-key"], "SUB");
        assert_eq!(alert["subtitle-loc-args"], json!(["x"]));
    }

    #[test]
    fn full() {
        let mut extra = Map::new();
        extra.insert("myapp".into(), json!({"is": "awesome"}));
        let notification = Notification {
            badge: Some(13),
            sound: Some("sounds/alert.mp3".into()),
            content_available: true,
            category: Some("my-category".into()),
            thread_id: Some("thread-id".into()),
            mutable_content: true,
            target_content_id: Some("target".into()),
            extra,
            ..Notification::new(Alert {
                action_loc_key: Some("action".into()),
                launch_image: Some("my/image.png".into()),
                ..Alert::new(Localized::with_args("Content", ["hoge"]))
                    .with_title(Localized::with_args("Test", ["foo", "bar"]))
            })
        };
        assert_eq!(
            notification.to_json(),
            json!({
                "aps": {
                    "alert": {
                        "title-loc-key": "Test",
                        "title-loc-args": ["foo", "bar"],
                        "loc-key": "Content",
                        "loc-args": ["hoge"],
                        "action-loc-key": "action",
                        "launch-image": "my/image.png",
                    },
                    "badge": 13,
                    "sound": "sounds/alert.mp3",
                    "content-available": 1,
                    "category": "my-category",
                    "thread-id": "thread-id",
                    "mutable-content": 1,
                    "target-content-id": "target",
                },
                "myapp": {"is": "awesome"},
            })
        );
    }

    #[test]
    fn zero_badge_clears_the_badge() {
        let notification = Notification {
            badge: Some(0),
            ..Notification::new(Alert::new("body"))
        };
        assert_eq!(notification.to_json()["aps"]["badge"], 0);
    }

    #[test]
    fn push_types() {
        assert_eq!(PushType::default(), PushType::Alert);
        assert_eq!(PushType::FileProvider.to_string(), "fileprovider");
        assert_eq!(PushType::Voip.max_payload_size(), 5120);
        assert_eq!(PushType::Mdm.max_payload_size(), 4096);
    }
}
