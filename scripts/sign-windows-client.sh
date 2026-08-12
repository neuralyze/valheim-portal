#!/usr/bin/env bash
set -euo pipefail

# Authenticode-sign the Windows client.
#
# Windows Defender flagged an unsigned build as Trojan:Win32/Bearfoos.A!ml and deleted it from
# %LOCALAPPDATA%, which broke the Desktop shortcut - the protocol handler was still registered but
# its target was gone. The detection is a machine-learning heuristic rather than a signature match,
# and the behaviour it keys on is the installer's by design: an unsigned binary that copies itself
# into AppData, registers a URL protocol pointing at the copy, downloads files, and starts another
# process. Signing removes the "unsigned" half of that profile, which is the half we control.
#
# Signing happens on Linux because that is where the client is built. osslsigncode writes the same
# Authenticode structure signtool.exe would.
#
#   sign-windows-client.sh FILE
#
# Credentials come from the environment so nothing secret enters the repository or the process list:
#
#   PORTAL_SIGNING_PKCS12       path to a .pfx/.p12 holding the certificate and key
#   PORTAL_SIGNING_PASSWORD     its password, if any
#   PORTAL_SIGNING_TIMESTAMP    RFC3161 timestamp server (a default is used when unset)
#
# Or, for a key held in hardware or a cloud HSM - which every public CA now requires:
#
#   PORTAL_SIGNING_PKCS11_MODULE  e.g. /usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so
#   PORTAL_SIGNING_PKCS11_CERT    certificate file matching the token key
#   PORTAL_SIGNING_PKCS11_KEY     key URI, e.g. pkcs11:object=Signing%20Key
#
# Absent both, the script exits 0 and says so: an unsigned build must stay possible, because a
# signing outage is not a reason to be unable to ship a fix.

FILE=${1:?"usage: sign-windows-client.sh FILE"}
TIMESTAMP=${PORTAL_SIGNING_TIMESTAMP:-http://timestamp.digicert.com}

if [[ ! -f "$FILE" ]]; then
    echo "nothing to sign: $FILE" >&2
    exit 1
fi

if ! command -v osslsigncode >/dev/null 2>&1; then
    echo "osslsigncode is not installed; leaving $FILE unsigned" >&2
    exit 0
fi

signed="$FILE.signed"

if [[ -n "${PORTAL_SIGNING_PKCS12:-}" ]]; then
    [[ -f "$PORTAL_SIGNING_PKCS12" ]] || { echo "signing certificate not found: $PORTAL_SIGNING_PKCS12" >&2; exit 1; }
    osslsigncode sign \
        -pkcs12 "$PORTAL_SIGNING_PKCS12" \
        ${PORTAL_SIGNING_PASSWORD:+-pass "$PORTAL_SIGNING_PASSWORD"} \
        -n "Valheim Profile Sync" \
        -i "https://valheim.neuralyze.com" \
        -ts "$TIMESTAMP" \
        -h sha256 \
        -in "$FILE" -out "$signed"
elif [[ -n "${PORTAL_SIGNING_PKCS11_MODULE:-}" ]]; then
    osslsigncode sign \
        -pkcs11module "$PORTAL_SIGNING_PKCS11_MODULE" \
        -certs "${PORTAL_SIGNING_PKCS11_CERT:?"PORTAL_SIGNING_PKCS11_CERT is required with a token"}" \
        -key "${PORTAL_SIGNING_PKCS11_KEY:?"PORTAL_SIGNING_PKCS11_KEY is required with a token"}" \
        -n "Valheim Profile Sync" \
        -i "https://valheim.neuralyze.com" \
        -ts "$TIMESTAMP" \
        -h sha256 \
        -in "$FILE" -out "$signed"
else
    echo "no signing credentials set; $FILE is unsigned" >&2
    exit 0
fi

mv -f -- "$signed" "$FILE"

# A signature that cannot be verified here will not be trusted there. The timestamp matters as much
# as the signature: without it the binary stops validating the day the certificate expires.
osslsigncode verify -in "$FILE" | sed -n '1,12p'
printf 'signed %s\n' "$FILE" >&2
