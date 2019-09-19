import datetime
from typing import Optional

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BACKEND = default_backend()


def gen_private_key() -> rsa.RSAPrivateKeyWithSerialization:
    return rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=BACKEND
    )


def gen_certificate(
    key: rsa.RSAPrivateKey,
    common_name: str,
    *,
    issuer: Optional[str] = None,
    sign_key: Optional[rsa.RSAPrivateKey] = None
) -> x509.Certificate:
    now = datetime.datetime.utcnow()
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer or common_name)])
        )
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(seconds=86400))
        .serial_number(x509.random_serial_number())
        .public_key(key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(private_key=sign_key or key, algorithm=hashes.SHA256(), backend=BACKEND)
    )


def create_client_cert() -> bytes:
    ca_key = gen_private_key()
    ca_cert = gen_certificate(ca_key, "certificate_authority")
    client_key = gen_private_key()
    client_cert = gen_certificate(
        client_key, "client", issuer="certificate_authority", sign_key=ca_key
    )
    return client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ) + client_cert.public_bytes(encoding=serialization.Encoding.PEM)
