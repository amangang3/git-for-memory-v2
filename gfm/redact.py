"""Strip secrets from log text before it reaches Claude or the memory store."""
import re

PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "[ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[API_KEY]"),
    (re.compile(r"(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[GITHUB_TOKEN]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[GOOGLE_KEY]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[SLACK_TOKEN]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "[JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[PRIVATE_KEY]"),
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\3"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:\s]+:)[^@\s]+@"), r"\1[REDACTED]@"),
]


def redact(text: str) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text
