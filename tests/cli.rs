#![cfg(all(unix, feature = "cli"))]

mod common;

use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Output;

use common::*;
use tokio::process::Command;

fn aapns() -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_aapns"));
    // `--server local` trusts tests/certs/ca.pem, relative to the working directory.
    command.current_dir(env!("CARGO_MANIFEST_DIR"));
    command.env_remove("CLIENT_CERT_PATH");
    command
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

fn assert_success(output: &Output) {
    assert!(
        output.status.success(),
        "stdout: {}\nstderr: {}",
        text(&output.stdout),
        text(&output.stderr)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn server() {
    let server = MockServer::start(Flavor::Ok).await;
    let output = aapns()
        .args([
            "server", "42", "Hello", "--title", "Hi", "--server", "local",
        ])
        .args(["--port", &server.port().to_string()])
        .args(["--client-cert-path", "tests/certs/client.pem"])
        .args([
            "--immediately",
            "--topic",
            "com.example.app",
            "--collapse-id",
            "c",
            "--expiration",
            "1",
        ])
        .output()
        .await
        .unwrap();
    assert_success(&output);
    assert_eq!(text(&output.stdout), format!("{APNS_ID}\n"));

    let requests = server.requests();
    assert_eq!(requests.len(), 1);
    let request = &requests[0];
    assert_eq!(request.path, "/3/device/42");
    assert_eq!(request.header("apns-priority"), Some("10"));
    assert_eq!(request.header("apns-topic"), Some("com.example.app"));
    assert_eq!(request.header("apns-collapse-id"), Some("c"));
    assert_eq!(request.header("apns-expiration"), Some("1"));
    assert_eq!(
        request.json(),
        serde_json::json!({"aps": {"alert": {"title": "Hi", "body": "Hello"}}})
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn client_cert_path_from_environment() {
    let server = MockServer::start(Flavor::Ok).await;
    let output = aapns()
        .args([
            "server",
            "42",
            "Hello",
            "--server",
            "local",
            "--port",
            &server.port().to_string(),
        ])
        .env("CLIENT_CERT_PATH", "tests/certs/client.pem")
        .output()
        .await
        .unwrap();
    assert_success(&output);
    assert_eq!(server.requests()[0].header("apns-priority"), Some("5"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rejected() {
    let server = MockServer::start(Flavor::BadToken).await;
    let output = aapns()
        .args([
            "server",
            "42",
            "Hello",
            "--server",
            "local",
            "--port",
            &server.port().to_string(),
        ])
        .args(["--client-cert-path", "tests/certs/client.pem"])
        .output()
        .await
        .unwrap();
    assert!(!output.status.success());
    assert_eq!(text(&output.stdout), "");
    assert!(
        text(&output.stderr).contains("BadDeviceToken"),
        "{}",
        text(&output.stderr)
    );
}

#[tokio::test]
async fn missing_client_certificate() {
    let output = aapns()
        .args(["server", "42", "Hello"])
        .output()
        .await
        .unwrap();
    assert!(!output.status.success());
    assert!(
        text(&output.stderr).contains("--client-cert-path"),
        "{}",
        text(&output.stderr)
    );
}

fn fake_xcrun(dir: &Path, exit_code: i32) {
    let script = format!(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/args\"\ncat \"$5\" > \"$(dirname \"$0\")/payload\"\nexit {exit_code}\n"
    );
    let path = dir.join("xcrun");
    std::fs::write(&path, script).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

fn path_with(dir: &Path) -> String {
    format!(
        "{}:{}",
        dir.display(),
        std::env::var("PATH").unwrap_or_default()
    )
}

#[tokio::test]
async fn simulator() {
    let dir = tempfile::tempdir().unwrap();
    fake_xcrun(dir.path(), 0);
    let output = aapns()
        .args([
            "simulator",
            "booted",
            "com.example.app",
            "Hello",
            "--title",
            "Hi",
        ])
        .env("PATH", path_with(dir.path()))
        .output()
        .await
        .unwrap();
    assert_success(&output);
    assert_eq!(text(&output.stdout), "");

    let args = std::fs::read_to_string(dir.path().join("args")).unwrap();
    let args: Vec<_> = args.lines().collect();
    assert_eq!(args[..4], ["simctl", "push", "booted", "com.example.app"]);
    assert!(args[4].ends_with("notification.apns"), "{args:?}");
    let payload = std::fs::read_to_string(dir.path().join("payload")).unwrap();
    assert_eq!(
        payload,
        r#"{"aps":{"alert":{"body":"Hello","title":"Hi"}}}"#
    );
}

#[tokio::test]
async fn simulator_failure() {
    let dir = tempfile::tempdir().unwrap();
    fake_xcrun(dir.path(), 1);
    let output = aapns()
        .args(["simulator", "booted", "com.example.app", "Hello"])
        .env("PATH", path_with(dir.path()))
        .output()
        .await
        .unwrap();
    assert!(!output.status.success());
    assert!(
        text(&output.stderr).contains("xcrun failed"),
        "{}",
        text(&output.stderr)
    );
}
