use std::fmt;

use serde_json::{Map, Value};

/// Represents a localized string to be used for the body or title of an [`Alert`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Localized {
    pub key: String,
    pub args: Vec<String>,
}

impl Localized {
    pub fn new(key: impl Into<String>) -> Self {
        Localized {
            key: key.into(),
            args: Vec::new(),
        }
    }

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Text {
    Plain(String),
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

/// Represents an alert, which can be used in [`Notification`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Alert {
    pub body: Text,
    pub title: Option<Text>,
    pub subtitle: Option<Text>,
    pub action_loc_key: Option<String>,
    pub launch_image: Option<String>,
}

impl Alert {
    pub fn new(body: impl Into<Text>) -> Self {
        Alert {
            body: body.into(),
            title: None,
            subtitle: None,
            action_loc_key: None,
            launch_image: None,
        }
    }

    pub fn with_title(mut self, title: impl Into<Text>) -> Self {
        self.title = Some(title.into());
        self
    }

    pub fn with_subtitle(mut self, subtitle: impl Into<Text>) -> Self {
        self.subtitle = Some(subtitle.into());
        self
    }

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

/// Enum holding possible types of push notifications
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum PushType {
    #[default]
    Alert,
    Background,
    Voip,
    Complication,
    FileProvider,
    Mdm,
}

impl PushType {
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

/// Represents a notification to send. For details on the parameters, please
/// refer to the Apple APNS documentation.
#[derive(Debug, Clone, PartialEq)]
pub struct Notification {
    pub alert: Alert,
    pub push_type: PushType,
    pub badge: Option<u32>,
    pub sound: Option<String>,
    pub content_available: bool,
    pub category: Option<String>,
    pub thread_id: Option<String>,
    pub mutable_content: bool,
    pub target_content_id: Option<String>,
    pub extra: Map<String, Value>,
}

impl Notification {
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

    pub fn encode(&self) -> Vec<u8> {
        encode_json(&self.to_json())
    }
}

pub(crate) fn encode_json(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).expect("JSON values always serialize")
}

/// ```compile_fail
/// aapns::models::Localized::with_args("foo", [1]);
/// ```
///
/// ```compile_fail
/// aapns::models::Alert::new();
/// ```
#[cfg(doctest)]
struct InvalidModels;

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
