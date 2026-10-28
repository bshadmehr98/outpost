"""Certificates and signatures, done with the openssl command line so there is nothing to install.

  pki/ca.crt            your private CA; every node trusts it           (commit this)
  secrets/ca.key        the CA's private key                            (never commit)
  pki/gitea.crt         Gitea's server certificate, signed by the CA    (commit this)
  secrets/gitea.key     Gitea's private key                             (never commit)
  pki/box.pub           public key that verifies box signatures         (commit this)
  secrets/box.key       ed25519 key that signs box manifests            (never commit)
"""

import subprocess
import tempfile
from pathlib import Path


def _openssl(*args: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(["openssl", *args], input=input, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed: {result.stderr.decode().strip()}")
    return result.stdout


def subject_hash(pem: str) -> str:
    """OpenSSL's subject hash of a certificate, the name it looks for in a CA directory: `<hash>.0`."""
    return _openssl("x509", "-noout", "-subject_hash", input=pem.encode()).decode().strip()


def ensure_ca(pki_dir: Path, secrets_dir: Path, name: str = "Outpost CA") -> Path:
    cert, key = pki_dir / "ca.crt", secrets_dir / "ca.key"
    if cert.exists() and key.exists():
        return cert
    pki_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    _openssl("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "3650", "-sha256",
             "-subj", f"/CN={name}", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-keyout", str(key), "-out", str(cert))
    key.chmod(0o600)
    return cert


def ensure_server_cert(pki_dir: Path, secrets_dir: Path, dns_name: str, ip: str | None, days: int = 825) -> tuple[Path, Path]:
    """Issue gitea.crt/gitea.key for dns_name (and ip) if missing or if the name changed."""
    cert, key = pki_dir / "gitea.crt", secrets_dir / "gitea.key"
    if cert.exists() and key.exists() and dns_name in _openssl("x509", "-in", str(cert), "-noout", "-ext", "subjectAltName").decode():
        return cert, key
    ensure_ca(pki_dir, secrets_dir)
    san = f"DNS:{dns_name}" + (f",IP:{ip}" if ip else "")
    with tempfile.TemporaryDirectory() as tmp:
        csr = Path(tmp) / "req.csr"
        ext = Path(tmp) / "ext.cnf"
        ext.write_text(f"subjectAltName={san}\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n")
        _openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={dns_name}", "-keyout", str(key), "-out", str(csr))
        _openssl("x509", "-req", "-in", str(csr), "-CA", str(pki_dir / "ca.crt"), "-CAkey", str(secrets_dir / "ca.key"),
                 "-CAcreateserial", "-days", str(days), "-sha256", "-extfile", str(ext), "-out", str(cert))
    key.chmod(0o600)
    return cert, key


def ensure_signing_key(pki_dir: Path, secrets_dir: Path) -> tuple[Path, Path]:
    pub, key = pki_dir / "box.pub", secrets_dir / "box.key"
    if not key.exists():
        secrets_dir.mkdir(parents=True, exist_ok=True)
        pki_dir.mkdir(parents=True, exist_ok=True)
        _openssl("genpkey", "-algorithm", "ed25519", "-out", str(key))
        key.chmod(0o600)
    if not pub.exists():
        _openssl("pkey", "-in", str(key), "-pubout", "-out", str(pub))
    return pub, key


def sign(path: Path, key: Path) -> Path:
    sig = path.with_suffix(".sig")
    _openssl("pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(path), "-out", str(sig))
    return sig


def verify(path: Path, sig: Path, pub: Path) -> bool:
    result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin", "-in", str(path), "-sigfile", str(sig)],
                            capture_output=True)
    return result.returncode == 0
