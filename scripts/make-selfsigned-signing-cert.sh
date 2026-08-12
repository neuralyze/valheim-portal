#!/usr/bin/env bash
set -euo pipefail

# A signing certificate you can use today, trusted only where you install it.
#
# Public certificate authorities now require the private key to live in hardware or a cloud HSM, so
# a purchased certificate is days of identity checks and a token in the post. This produces a
# self-signed one in a minute. It does NOT satisfy SmartScreen and it will not help anyone else -
# but on the machines where you install it, the client stops being an unknown publisher, which is
# the specific property Defender's heuristic keys on.
#
#   make-selfsigned-signing-cert.sh [OUTPUT_DIR]
#
# Then, ON THE WINDOWS MACHINE, in an elevated PowerShell:
#
#   Import-Certificate -FilePath .\valheim-profile-sync-signing.crt -CertStoreLocation Cert:\LocalMachine\Root
#   Import-Certificate -FilePath .\valheim-profile-sync-signing.crt -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
#
# Only the .crt goes to Windows. The .p12 holds the private key and stays on the build host.

OUT=${1:-"$HOME/.valheim-portal-signing"}
DAYS=${PORTAL_SIGNING_DAYS:-1095}
NAME=${PORTAL_SIGNING_NAME:-"Neuralyze Valheim Portal"}

mkdir -p "$OUT"
chmod 700 "$OUT"

key="$OUT/valheim-profile-sync-signing.key"
crt="$OUT/valheim-profile-sync-signing.crt"
p12="$OUT/valheim-profile-sync-signing.p12"

if [[ -f "$p12" ]]; then
    echo "a signing bundle already exists at $p12 - refusing to overwrite it" >&2
    echo "delete it deliberately if you want a new identity; re-signing with a new certificate" >&2
    echo "means re-importing it on every machine that trusts the old one" >&2
    exit 1
fi

# codeSigning EKU, and a basicConstraints CA:true so the same certificate can be imported as a root
# on the machines that should trust it. A leaf issued by a private CA would be tidier; for two or
# three personal machines the extra key to guard is not worth it.
openssl req -x509 -newkey rsa:3072 -sha256 -days "$DAYS" -nodes \
    -keyout "$key" -out "$crt" \
    -subj "/CN=$NAME/O=$NAME" \
    -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" 2>/dev/null

PASSWORD=${PORTAL_SIGNING_PASSWORD:-}
openssl pkcs12 -export -out "$p12" -inkey "$key" -in "$crt" \
    -name "$NAME" -passout "pass:${PASSWORD}"

chmod 600 "$key" "$p12"

cat <<TXT
created:
  $p12   sign with this   (keep on the build host)
  $crt   trust this       (copy to Windows)

to sign:
  export PORTAL_SIGNING_PKCS12="$p12"
  ${PASSWORD:+export PORTAL_SIGNING_PASSWORD='...'}
  scripts/build-windows-client.sh

fingerprint (check this matches what Windows shows after import):
  $(openssl x509 -in "$crt" -noout -fingerprint -sha256 | cut -d= -f2)
TXT
