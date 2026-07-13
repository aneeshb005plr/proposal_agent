# app/security/encryption.py
#
# RFP Analyzer's OWN copy of the SAME Fernet-based encryption used
# by knowledge-sync-worker's app/security/encryption.py — deliberate
# duplication (small file, same "duplicate rather than share as a
# library" call already made for the parser and job_repository
# elsewhere in this project), NOT a different mechanism.
#
# REGISTRY_ENCRYPTION_KEY must be the SAME literal value as the
# worker's own setting — this agent's API encrypts source config
# secrets at write time (POST/PATCH /knowledge/sources), the worker
# decrypts them at read time when actually running a sync. Two
# services, one shared key, by necessity — RFP Analyzer is now the
# one CREATING encrypted entries the worker later consumes.

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("app.security.encryption")

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.REGISTRY_ENCRYPTION_KEY:
            raise RuntimeError(
                "REGISTRY_ENCRYPTION_KEY is not set — cannot encrypt "
                "knowledge source secrets. This MUST match the same "
                "key configured on knowledge-sync-worker, or the "
                "worker will fail to decrypt anything this API stores."
            )
        _fernet = Fernet(settings.REGISTRY_ENCRYPTION_KEY.get_secret_value().encode())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Not currently used by any route (this agent only ever
    ENCRYPTS, never needs to read its own secrets back) — kept for
    symmetry/completeness and in case a future admin route needs to
    verify a stored value."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt — wrong REGISTRY_ENCRYPTION_KEY, or "
            "the stored value is corrupted/not encrypted with this key."
        ) from e