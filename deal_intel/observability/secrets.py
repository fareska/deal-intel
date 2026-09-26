import re

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:x-api-key|api[_-]?key|password|secret)\s*[=:]\s*\S{8,}"),
    # Credentials inside a connection URL, such as postgresql://user:password@host.
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
)


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
