//! TLS configuration.
//!
//! Connections use [rustls], which only supports TLS 1.2 and 1.3. ALPN is
//! always set to `h2`, whatever the configuration passed in.

use std::path::Path;
use std::sync::Arc;

use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::{ClientConfig, RootCertStore};

use crate::errors::{Error, Result};

/// The crypto provider used by aapns, independent of the process default.
pub fn crypto_provider() -> Arc<rustls::crypto::CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

/// Creates a TLS client configuration suitable for APNs.
///
/// The server is verified against the platform's trust store, plus the
/// certificates in `ca_file` if given. If `client_cert_path` is given, the
/// client certificate chain and its private key are loaded from that PEM
/// file, the format of an Apple push certificate exported to PEM.
pub fn create_tls_config(
    client_cert_path: Option<&Path>,
    ca_file: Option<&Path>,
) -> Result<Arc<ClientConfig>> {
    let mut roots = RootCertStore::empty();
    let native = rustls_native_certs::load_native_certs();
    for error in &native.errors {
        tracing::warn!(%error, "failed to load some platform root certificates");
    }
    roots.add_parsable_certificates(native.certs);
    if let Some(ca_file) = ca_file {
        for cert in load_certificates(ca_file)? {
            roots.add(cert).map_err(|e| {
                Error::Tls(format!(
                    "invalid CA certificate in {}: {e}",
                    ca_file.display()
                ))
            })?;
        }
    }

    let builder = ClientConfig::builder_with_provider(crypto_provider())
        .with_safe_default_protocol_versions()
        .map_err(|e| Error::Tls(e.to_string()))?
        .with_root_certificates(roots);
    let mut config = match client_cert_path {
        Some(path) => {
            let (chain, key) = load_client_identity(path)?;
            builder.with_client_auth_cert(chain, key).map_err(|e| {
                Error::Tls(format!(
                    "invalid client certificate {}: {e}",
                    path.display()
                ))
            })?
        }
        None => builder.with_no_client_auth(),
    };
    config.alpn_protocols = vec![b"h2".to_vec()];
    Ok(Arc::new(config))
}

/// Loads a client certificate chain and its private key from a single PEM
/// file. Text outside of PEM sections, such as the `Bag Attributes` written by
/// `openssl pkcs12`, is ignored. Encrypted private keys are not supported.
pub fn load_client_identity(
    path: &Path,
) -> Result<(Vec<CertificateDer<'static>>, PrivateKeyDer<'static>)> {
    let chain = load_certificates(path)?;
    let key = PrivateKeyDer::from_pem_file(path).map_err(|e| match e {
        rustls::pki_types::pem::Error::NoItemsFound => {
            Error::Tls(format!("no private key found in {}", path.display()))
        }
        e => Error::Tls(format!(
            "failed to read private key from {}: {e}",
            path.display()
        )),
    })?;
    Ok((chain, key))
}

/// Loads all certificates from a PEM file.
pub fn load_certificates(path: &Path) -> Result<Vec<CertificateDer<'static>>> {
    let read_error = |e: rustls::pki_types::pem::Error| {
        Error::Tls(format!("failed to read {}: {e}", path.display()))
    };
    let certs = CertificateDer::pem_file_iter(path)
        .map_err(read_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(read_error)?;
    if certs.is_empty() {
        return Err(Error::Tls(format!(
            "no certificate found in {}",
            path.display()
        )));
    }
    Ok(certs)
}

/// Returns `config` with ALPN set to `h2`, cloning it only if necessary.
pub(crate) fn with_h2_alpn(config: Arc<ClientConfig>) -> Arc<ClientConfig> {
    if config.alpn_protocols == [b"h2"] {
        config
    } else {
        let mut config = (*config).clone();
        config.alpn_protocols = vec![b"h2".to_vec()];
        Arc::new(config)
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    fn certs_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/certs")
    }

    #[test]
    fn loads_combined_pem() {
        let (chain, _key) = load_client_identity(&certs_dir().join("client.pem")).unwrap();
        assert_eq!(chain.len(), 1);
    }

    #[test]
    fn ignores_bag_attributes() {
        let pem = std::fs::read_to_string(certs_dir().join("client.pem")).unwrap();
        let exported = format!(
            "Bag Attributes\n    friendlyName: Apple Development IOS Push Services\n\
             subject=/UID=test.company.app/CN=Apple Development IOS Push Services\n{pem}"
        );
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("exported.pem");
        std::fs::write(&path, exported).unwrap();
        load_client_identity(&path).unwrap();
    }

    #[test]
    fn missing_key() {
        let error = load_client_identity(&certs_dir().join("ca.pem")).unwrap_err();
        assert!(
            error.to_string().contains("no private key found"),
            "{error}"
        );
    }

    #[test]
    fn missing_certificate() {
        let error = load_client_identity(&certs_dir().join("server-key.pem")).unwrap_err();
        assert!(
            error.to_string().contains("no certificate found"),
            "{error}"
        );
    }

    #[test]
    fn missing_file() {
        let error = load_client_identity(&certs_dir().join("nope.pem")).unwrap_err();
        assert!(matches!(error, Error::Tls(_)), "{error}");
    }

    #[test]
    fn config_has_h2_alpn() {
        let config = create_tls_config(
            Some(&certs_dir().join("client.pem")),
            Some(&certs_dir().join("ca.pem")),
        )
        .unwrap();
        assert_eq!(config.alpn_protocols, [b"h2"]);
    }

    #[test]
    fn alpn_is_forced() {
        let mut config = (*create_tls_config(None, None).unwrap()).clone();
        config.alpn_protocols = vec![b"http/1.1".to_vec()];
        assert_eq!(with_h2_alpn(Arc::new(config)).alpn_protocols, [b"h2"]);
    }
}
