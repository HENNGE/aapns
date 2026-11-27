#!/usr/bin/env bash
# Test PKI: ca.pem, server.pem with server-key.pem, and client.pem with its key
set -euo pipefail
cd "$(dirname "$0")"

days=36500
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

serial() { echo "0x$(openssl rand -hex 16)"; }

# Test CA
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$work/ca-key.pem"
cat > "$work/ca.ext" <<EXT
[req]
distinguished_name = dn
[dn]
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EXT
openssl req -x509 -new -key "$work/ca-key.pem" -sha256 -days "$days" -set_serial "$(serial)" \
  -subj "/CN=aapns test CA" -config "$work/ca.ext" -extensions v3_ca -out ca.pem

# Server certificate (localhost)
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out server-key.pem
openssl req -new -key server-key.pem -subj "/CN=localhost" -out "$work/server.csr"
cat > "$work/server.ext" <<EXT
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, IP:127.0.0.1, IP:::1
authorityKeyIdentifier = keyid
subjectKeyIdentifier = hash
EXT
openssl x509 -req -in "$work/server.csr" -CA ca.pem -CAkey "$work/ca-key.pem" -set_serial "$(serial)" \
  -days "$days" -sha256 -extfile "$work/server.ext" -out server.pem

# Client certificate (RSA, like Apple push certificates), cert and key in one file
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$work/client-key.pem"
openssl req -new -key "$work/client-key.pem" \
  -subj "/CN=Apple Development IOS Push Services: test.company.app/OU=TEST99TEST/C=US" \
  -out "$work/client.csr"
cat > "$work/client.ext" <<EXT
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = clientAuth
authorityKeyIdentifier = keyid
subjectKeyIdentifier = hash
EXT
openssl x509 -req -in "$work/client.csr" -CA ca.pem -CAkey "$work/ca-key.pem" -set_serial "$(serial)" \
  -days "$days" -sha256 -extfile "$work/client.ext" -out "$work/client-cert.pem"
cat "$work/client-cert.pem" "$work/client-key.pem" > client.pem
