"""Optional: remember keys in the operating system's keychain.

Off by default. Works only if the ``keyring`` package is installed
(``pip install keyring``), which stores secrets in macOS Keychain, Windows
Credential Manager or the Linux Secret Service. This app never writes a key
to a plain file.
"""

from __future__ import annotations

SERVICE = "garmin-ai-coach"

try:  # pragma: no cover - depends on the user's machine
    import keyring  # type: ignore
    from keyring.errors import KeyringError  # type: ignore

    _backend = keyring.get_keyring()
    AVAILABLE = "fail" not in type(_backend).__module__.lower()
except Exception:  # keyring missing or no usable backend
    keyring = None  # type: ignore
    KeyringError = Exception  # type: ignore
    AVAILABLE = False


def load(name: str) -> str | None:
    if not AVAILABLE:
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError:
        return None


def save(name: str, secret: str) -> bool:
    if not AVAILABLE or not secret:
        return False
    try:
        keyring.set_password(SERVICE, name, secret)
        return True
    except KeyringError:
        return False


def delete(name: str) -> None:
    if not AVAILABLE:
        return
    try:
        keyring.delete_password(SERVICE, name)
    except Exception:
        pass
