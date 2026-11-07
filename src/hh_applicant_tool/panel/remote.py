"""Доступ к панели с других устройств: токен, HTTPS, адреса в сети.

Сценарий: панель и локальные нейронки крутятся на домашнем ПК, а вы
открываете панель с ноутбука. Панель запускает любые команды и видит
ключи, поэтому наружу она слушает только с токеном и по HTTPS:
без шифрования токен и cookie уходили бы по Wi-Fi открытым текстом.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import secrets
import socket
import ssl
from pathlib import Path

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TOKEN_FILE = "panel-token.txt"
TLS_DIR = "panel-tls"


def is_local(host: str) -> bool:
    return host in LOCAL_HOSTS


def lan_addresses() -> list[str]:
    """IPv4-адреса машины в локальных сетях (для подсказки «откройте с мака»)."""
    found: set[str] = set()
    # Адрес исходящего интерфейса: соединение UDP ничего не отправляет
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    out = []
    for a in sorted(found):
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        # 198.18.0.0/15 занимают TUN-интерфейсы VPN-клиентов (Mihomo, Clash):
        # с другого устройства по такому адресу не зайти
        if ip.is_private and not ip.is_loopback and ip not in ipaddress.ip_network("198.18.0.0/15"):
            out.append(a)
    return out


def ensure_token(config_dir: Path, token: str | None) -> tuple[str, bool]:
    """Токен из флага или файла, иначе новый. Возвращает (токен, создан ли)."""
    if token:
        return token, False
    path = config_dir / TOKEN_FILE
    try:
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            return saved, False
    except FileNotFoundError:
        pass
    new = secrets.token_urlsafe(18)
    config_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(new + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return new, True


def ensure_certificate(config_dir: Path, extra_hosts: list[str]) -> tuple[Path, Path]:
    """Самоподписанный сертификат для HTTPS в домашней сети.

    Создаётся один раз и лежит в CONFIG_DIR/panel-tls. Если поменялся
    IP машины, сертификат пересоздаётся, чтобы браузер не ругался на имя.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    d = config_dir / TLS_DIR
    cert_path, key_path = d / "cert.pem", d / "key.pem"
    names = sorted({"localhost", socket.gethostname(), *extra_hosts, "127.0.0.1"})

    if cert_path.exists() and key_path.exists():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(v) for v in san.get_values_for_type(x509.IPAddress)} | set(san.get_values_for_type(x509.DNSName))
            fresh = cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
            if fresh and set(names) <= have:
                return cert_path, key_path
        except (ValueError, x509.ExtensionNotFound):
            pass

    key = ec.generate_private_key(ec.SECP256R1())
    alt: list[x509.GeneralName] = []
    for n in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            alt.append(x509.DNSName(n))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hh-applicant-tool panel")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        # Не больше 825 дней: дольше браузеры на маке не принимают
        .not_valid_after(now + dt.timedelta(days=820))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    d.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def fingerprint(cert_path: Path) -> str:
    """SHA-256 отпечаток сертификата: сверить в браузере на маке."""
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i : i + 2] for i in range(0, len(h), 2))


def ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return ctx
