"""Outpost's own CA, Gitea's certificate and the key that signs the box, made with the openssl command line.

    ensure_ca(workspace)                                   # pki/ca.crt + secrets/ca.key, once
    ensure_gitea_cert(workspace, "gitea.internal", ip)     # pki/gitea.crt + secrets/gitea.key
    public_key, signing_key = ensure_box_signing_key(workspace)
    signature = sign_file(Path("box/manifest.json"), signing_key)
    signature_is_valid(Path("box/manifest.json"), signature, public_key)

The files:

    pki/ca.crt            your private CA; every node trusts it           (commit this)
    secrets/ca.key        the CA's private key                            (never commit)
    pki/gitea.crt         Gitea's server certificate, signed by the CA    (commit this)
    secrets/gitea.key     Gitea's private key                             (never commit)
    pki/box.pub           public key that checks box signatures           (commit this)
    secrets/box.key       ed25519 key that signs box manifests            (never commit)

openssl is on every laptop that runs Outpost, so there is nothing to install. Every openssl call is
logged at DEBUG on the `outpost.pki` logger.
"""

import ipaddress
import logging
import subprocess
import tempfile
import time
from pathlib import Path

from outpost.workspace import Workspace

logger = logging.getLogger(__name__)

CA_DAYS = 3650
GITEA_CERT_DAYS = 825  # the longest validity Apple and the browsers accept for a server certificate


def ensure_ca(workspace: Workspace, name: str = "Outpost CA") -> Path:
    """Make the CA (pki/ca.crt and secrets/ca.key) unless both are there. Returns the certificate's path."""
    cert, key = workspace.ca_cert, workspace.ca_key
    if cert.exists() and key.exists():
        return cert
    workspace.pki_dir.mkdir(parents=True, exist_ok=True)
    workspace.secrets_dir.mkdir(parents=True, exist_ok=True)
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-nodes",
        "-days",
        str(CA_DAYS),
        "-sha256",
        "-subj",
        f"/CN={name}",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout",
        str(key),
        "-out",
        str(cert),
    )
    key.chmod(0o600)
    return cert


def ensure_gitea_cert(
    workspace: Workspace, name: str, ip: str | None, days: int = GITEA_CERT_DAYS
) -> tuple[Path, Path]:
    """Issue pki/gitea.crt and secrets/gitea.key for name (and ip), signed by the CA (made first if needed).

    An existing certificate is kept when it names exactly this name and ip; a changed `gitea.name` or
    `gitea.ip` in servers.yml gets a new one.

    Returns:
        (certificate, key).
    """
    cert, key = workspace.gitea_cert, workspace.gitea_key
    wanted = _subject_alt_names(name, ip)
    if cert.exists() and key.exists() and _names_in_certificate(cert) == wanted:
        return cert, key
    ensure_ca(workspace)
    with tempfile.TemporaryDirectory() as tmp:
        request = Path(tmp) / "req.csr"
        extensions = Path(tmp) / "ext.cnf"
        extensions.write_text(
            f"subjectAltName={','.join(sorted(wanted))}\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n"
        )
        _openssl(
            "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
            "-keyout", str(key), "-out", str(request),
        )  # fmt: skip
        _openssl(
            "x509", "-req", "-in", str(request), "-CA", str(workspace.ca_cert), "-CAkey", str(workspace.ca_key),
            "-CAcreateserial", "-days", str(days), "-sha256", "-extfile", str(extensions), "-out", str(cert),
        )  # fmt: skip
    key.chmod(0o600)
    return cert, key


def ensure_box_signing_key(workspace: Workspace) -> tuple[Path, Path]:
    """Make the ed25519 key that signs the box (secrets/box.key) and its public half (pki/box.pub), once.

    Returns:
        (public key, signing key).
    """
    public, key = workspace.box_public_key, workspace.box_signing_key
    if not key.exists():
        workspace.secrets_dir.mkdir(parents=True, exist_ok=True)
        workspace.pki_dir.mkdir(parents=True, exist_ok=True)
        _openssl("genpkey", "-algorithm", "ed25519", "-out", str(key))
        key.chmod(0o600)
    if not public.exists():
        _openssl("pkey", "-in", str(key), "-pubout", "-out", str(public))
    return public, key


def sign_file(path: Path, key: Path) -> Path:
    """Sign a file with an ed25519 key; the signature goes next to it, as <name>.sig. Returns its path."""
    signature = path.with_suffix(".sig")
    _openssl("pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(path), "-out", str(signature))
    return signature


def signature_is_valid(path: Path, signature: Path, public_key: Path) -> bool:
    """True when signature is path's signature by the key whose public half is public_key."""
    check = ["pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", str(path)]
    return _run_openssl([*check, "-sigfile", str(signature)]).returncode == 0


def subject_hash(pem: str) -> str:
    """OpenSSL's subject hash of a certificate, the name it looks for in a CA folder: `<hash>.0`."""
    return _openssl("x509", "-noout", "-subject_hash", input=pem.encode()).decode().strip()


def _subject_alt_names(name: str, ip: str | None) -> set[str]:
    """The SAN entries Gitea's certificate must have, as openssl's config writes them: DNS:x, IP:y."""
    return {f"DNS:{name}"} | ({f"IP:{ipaddress.ip_address(ip)}"} if ip else set())


def _names_in_certificate(cert: Path) -> set[str]:
    """The SAN entries of a certificate, in the form _subject_alt_names() makes."""
    text = _openssl("x509", "-in", str(cert), "-noout", "-ext", "subjectAltName").decode()
    names = set()
    for entry in text.splitlines()[-1].split(","):
        kind, _, value = entry.strip().partition(":")
        if kind == "DNS":
            names.add(f"DNS:{value}")
        elif kind == "IP Address":
            names.add(f"IP:{ipaddress.ip_address(value)}")
    return names


def _openssl(*args: str, input: bytes | None = None) -> bytes:
    """Run openssl and return what it printed.

    Raises:
        RuntimeError: openssl failed; the message has its error.
    """
    result = _run_openssl(list(args), input)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed: {result.stderr.decode().strip()}")
    return result.stdout


def _run_openssl(args: list[str], input: bytes | None = None) -> subprocess.CompletedProcess:
    started = time.monotonic()
    result = subprocess.run(["openssl", *args], input=input, capture_output=True)
    logger.debug("openssl %s: exit %d (%.1fs)", " ".join(args[:2]), result.returncode, time.monotonic() - started)
    return result
