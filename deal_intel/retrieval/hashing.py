import hashlib
import json


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_sha256(value: object) -> str:
    """Hash of a canonical JSON rendering, so equal values hash equally on every machine."""
    canonical = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return sha256_hex(canonical.encode("utf-8"))
