"""Generate a local certificate authority and a server certificate for QMS.

Why this exists
---------------
A browser only hands over a camera on a **secure context**. `http://192.168.x.x`
is not one, so scanning with a phone camera is impossible over plain HTTP.

There is no flag for it on iOS: Safari does not expose `navigator.mediaDevices`
on a page whose certificate it does not fully trust — not even after tapping
through the warning. So the phone has to genuinely trust us, which means a real
certificate, which means we have to be a real (tiny, local) certificate authority.

That is all this does: one CA, one server certificate signed by it, and the CA
handed to the phone once. ~60 seconds per phone, then the camera works forever,
offline, with no tunnel and no purchase.

    ./venv/bin/python make_cert.py              # create or refresh if needed
    ./venv/bin/python make_cert.py --force      # regenerate from scratch
    ./venv/bin/python make_cert.py --print      # show what is in the cert

Writes into certs/ next to this script:
    ca.crt      the CA certificate — this is the one a phone installs
    ca.key      the CA private key (0600) — never leaves the store box
    server.crt  the certificate the server presents
    server.key  the server private key (0600)

Idempotent: if the existing certificate still covers every address this machine
answers on, nothing is touched.
"""

import datetime
import ipaddress
import os
import socket
import sys

import netinfo

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
# Overridable so the test suite can generate into a throwaway directory without
# touching the certificates a store's phones have already installed.
CERT_DIR = os.environ.get("QMS_CERT_DIR") or os.path.join(HERE, "certs")
CA_CRT = os.path.join(CERT_DIR, "ca.crt")
CA_KEY = os.path.join(CERT_DIR, "ca.key")
SRV_CRT = os.path.join(CERT_DIR, "server.crt")
SRV_KEY = os.path.join(CERT_DIR, "server.key")

# Chrome refuses certificates valid for more than 398 days; iOS is stricter still
# about how long a leaf may live. Stay well inside both — regenerating is cheap.
LEAF_DAYS = 397
CA_DAYS = 3650

# Whose certificate this is. It lands in the Organization field of both the CA
# and the server certificate, so it is what a phone shows when someone opens the
# certificate details — and it is what appears on the iOS profile install screen.
# Overridable per store so an outlet can carry the store's own name instead.
ORG = os.environ.get("QMS_CERT_ORG") or "Zairi Khaidzir @ Mozaiz"


def say(msg):
    print("  " + msg)


def local_addresses():
    """Every address and name this machine answers on — see netinfo.py.

    The certificate has to name the address the *phone* will type. A store box on
    DHCP changes address eventually, so this is recomputed on every run and the
    certificate is rebuilt when the set of addresses moves — otherwise the phone
    keeps trusting a certificate for an address the server no longer answers on,
    and the camera silently stops working weeks later.
    """
    return netinfo.all_names_and_ips()


def build_san(names, ips):
    entries = [x509.DNSName(n) for n in sorted(names)]
    for ip in sorted(ips):
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(ip.split("%")[0])))
        except ValueError:
            pass
    return x509.SubjectAlternativeName(entries)


def _write(path, data, mode):
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_ca():
    key = new_key()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "QMS Store Certificate Authority"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))   # tolerate clock skew
        .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write(CA_KEY, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()), 0o600)
    _write(CA_CRT, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return key, cert


def make_server(ca_key, ca_cert, names, ips):
    key = new_key()
    host = socket.gethostname() or "qms"
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, host),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ), critical=True)
        # iOS 13+ ignores the Common Name entirely and wants the SAN. Without
        # this the phone shows a certificate error no matter what we install.
        .add_extension(build_san(names, ips), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write(SRV_KEY, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()), 0o600)
    _write(SRV_CRT, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return cert


def read_cert(path):
    with open(path, "rb") as fh:
        return x509.load_pem_x509_certificate(fh.read())


def covered_by(cert):
    """Which names and addresses the existing certificate covers."""
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    names = set(san.get_values_for_type(x509.DNSName))
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    return names, ips


def main():
    force = "--force" in sys.argv
    show = "--print" in sys.argv
    names, ips = local_addresses()

    print("==> QMS certificate")
    say(f"this machine answers as: {', '.join(sorted(names))}")
    say(f"and on these addresses:  {', '.join(sorted(ips)) or '(none found)'}")

    if show:
        if not os.path.exists(SRV_CRT):
            say("no certificate yet")
            return 1
        c = read_cert(SRV_CRT)
        n, i = covered_by(c)
        say(f"expires {c.not_valid_after_utc.date()}")
        say(f"names: {', '.join(sorted(n))}")
        say(f"addresses: {', '.join(sorted(i)) or '(none)'}")

    if not force:
        usable = os.path.exists(SRV_CRT) and os.path.exists(SRV_KEY) \
                 and os.path.exists(CA_CRT) and os.path.exists(CA_KEY)
        if usable:
            cert = read_cert(SRV_CRT)
            have_n, have_i = covered_by(cert)
            expiry = cert.not_valid_after_utc
            soon = expiry - datetime.datetime.now(datetime.timezone.utc)
            missing_n = names - have_n
            missing_i = ips - have_i

            # The name is part of the certificate, and a certificate's subject
            # cannot be edited — so a changed name is a changed CA. It has to be
            # checked HERE, in the same decision as the addresses, or the
            # "everything is fine" shortcut below returns before anyone notices
            # and the new name never reaches a single phone.
            ca_org_changed = False
            if os.path.exists(CA_CRT):
                ca = read_cert(CA_CRT)
                _o = ca.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
                ca_org_changed = (_o[0].value if _o else "") != ORG

            if not missing_n and not missing_i and not ca_org_changed and soon.days > 30:
                say(f"already valid for {soon.days} more days, covers everything — nothing to do")
                say(f"issued to {ORG}")
                return 0

            reasons = []
            if ca_org_changed:
                reasons.append(f'the name changed to "{ORG}" — this rebuilds the CA, '
                               f"so every phone must install the certificate again")
            if missing_n:
                reasons.append("new name(s): " + ", ".join(sorted(missing_n)))
            if missing_i:
                reasons.append("new address(es): " + ", ".join(sorted(missing_i)))
            if soon.days <= 30:
                reasons.append(f"expires in {soon.days} days")
            say("refreshing — " + "; ".join(reasons))

    os.makedirs(CERT_DIR, exist_ok=True)

    # Keep the CA if we already have one. Phones have installed it; throwing it
    # away would silently invalidate every phone in the store and nobody would
    # know why the cameras stopped working.
    reuse_ca = os.path.exists(CA_CRT) and os.path.exists(CA_KEY) and not force
    if reuse_ca:
        ca_cert = read_cert(CA_CRT)
        _o = ca_cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
        existing_org = _o[0].value if _o else ""
        if existing_org != ORG:
            # A certificate's subject cannot be edited, so this is a different
            # CA. Every phone that installed the old one stops trusting the
            # server, and the only cure is re-installing on each phone — so say
            # that plainly rather than swapping it out quietly.
            say(f'the CA says "{existing_org}" but this install is set to "{ORG}"')
            say("that is a DIFFERENT certificate authority, not a rename:")
            say("  every phone that already installed the old one must install the new one")
            reuse_ca = False
    if reuse_ca:
        with open(CA_KEY, "rb") as fh:
            ca_key = serialization.load_pem_private_key(fh.read(), password=None)
        say(f"keeping the existing CA ({ORG}) — phones have already installed it")
    else:
        ca_key, ca_cert = make_ca()
        say(f"created a new certificate authority for {ORG}")

    cert = make_server(ca_key, ca_cert, names, ips)

    say(f"server certificate written, expires {cert.not_valid_after_utc.date()}")
    say("")
    say("Next: run QMS over HTTPS, then have each scanning phone install certs/ca.crt.")
    say("Open  https://<this-machine>:8443/setup/phone  for the guided steps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
