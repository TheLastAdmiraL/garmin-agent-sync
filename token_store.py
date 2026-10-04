"""Windows DPAPI protection for the Garmin session token.

The encrypted blob can only be decrypted by the same Windows user profile that
created it. It is deliberately stored outside the project directory.
"""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


_MAGIC = b"GARMIN_CODEX_DPAPI_V1\0"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


# ctypes otherwise assumes 32-bit integer arguments and return values.  That can
# truncate pointers on 64-bit Windows, particularly while decrypting the token.
_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_blob_pointer = ctypes.POINTER(_DataBlob)
_crypt32.CryptProtectData.argtypes = [
    _blob_pointer,
    wintypes.LPCWSTR,
    _blob_pointer,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    _blob_pointer,
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    _blob_pointer,
    ctypes.POINTER(wintypes.LPWSTR),
    _blob_pointer,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    _blob_pointer,
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def token_path() -> Path:
    """Return a per-user location that is outside the repository."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable; cannot choose a safe token location.")
    return Path(local_app_data).resolve() / "GarminCodex" / "garmin-token.bin"


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value, len(value))
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(plaintext: bytes) -> bytes:
    input_blob, _input_buffer = _blob(plaintext)
    output_blob = _DataBlob()
    result = _crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        _kernel32.LocalFree(output_blob.pbData)


def _unprotect(ciphertext: bytes) -> bytes:
    input_blob, _input_buffer = _blob(ciphertext)
    output_blob = _DataBlob()
    result = _crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        _kernel32.LocalFree(output_blob.pbData)


def save(token_json: str) -> Path:
    """Encrypt and atomically save the token for the current Windows user."""
    if not token_json or not token_json.lstrip().startswith("{"):
        raise ValueError("Refusing to save an invalid Garmin token payload.")
    destination = token_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _MAGIC + _protect(token_json.encode("utf-8"))
    fd, temporary_name = tempfile.mkstemp(prefix=".garmin-token-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as token_file:
            token_file.write(encrypted)
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def load() -> str:
    """Load and decrypt the current user's token without logging it."""
    source = token_path()
    try:
        payload = source.read_bytes()
    except FileNotFoundError as error:
        raise RuntimeError("No Garmin login token is saved. Run with --login first.") from error
    if not payload.startswith(_MAGIC):
        raise RuntimeError("The saved Garmin token has an unrecognized format. Run --login again.")
    plaintext = _unprotect(payload[len(_MAGIC) :]).decode("utf-8")
    if not plaintext.lstrip().startswith("{"):
        raise RuntimeError("The saved Garmin token is invalid. Run --login again.")
    return plaintext
