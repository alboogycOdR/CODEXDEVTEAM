"""Provider-neutral detector for common credentials and private keys."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretPattern:
    name: str
    expression: re.Pattern[str]


_PATTERNS = (
    SecretPattern("Anthropic/OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    SecretPattern("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    SecretPattern("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    SecretPattern("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("Private key block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    SecretPattern("Telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b")),
    SecretPattern("Generic assigned secret", re.compile(
        r"(?:password|passwd|secret|api[_-]?key|auth[_-]?token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]", re.I)),
)


def find_secrets(text: str) -> tuple[str, ...]:
    """Return pattern labels only; never return or log matched credential text."""
    if not text:
        return ()
    return tuple(pattern.name for pattern in _PATTERNS if pattern.expression.search(text))
