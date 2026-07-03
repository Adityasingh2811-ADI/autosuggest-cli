"""
Secret redaction — scrub credentials out of command strings before they are
stored in the database (and therefore before they can be replayed as
suggestions).

Single public function: redact(command) -> str
Returns the command with secret values masked, or "" if the whole command
matches a denylist (caller should drop empty results, never store them).

Redaction happens at capture time so secrets never touch disk.
"""

import re

MASK = "***"

# Commands that are too sensitive to store at all — drop the whole line.
_DENYLIST = (
    re.compile(r"\bp4\b.*\s-P\s", re.IGNORECASE),          # p4 -P <ticket/pass>
    re.compile(r"\b(curl|wget)\b.*\s--?u(ser)?\s*\S+:\S+"),  # user:pass creds
)

# Flag-style secrets: -p secret / --password=secret / --token secret …
_FLAG = re.compile(
    r"(--?(?:p|pass(?:word)?|token|secret|api[-_]?key|apikey|auth|"
    r"access[-_]?key|client[-_]?secret|key)\b)"
    r"(\s*=\s*|\s+)"
    r"(\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)

# KEY=VALUE secrets in the environment style: AWS_SECRET_ACCESS_KEY=… PASSWORD=…
_ENV = re.compile(
    r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|"
    r"ACCESS_KEY|PRIVATE_KEY)[A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)

# Authorization headers: -H "Authorization: Bearer …"
_AUTH = re.compile(r"(Authorization:\s*\S+\s+)[^\s\"']+", re.IGNORECASE)

# user:pass@host inside a URL
_URLCRED = re.compile(r"(://[^\s:/@]+:)[^\s@/]+(@)")

# sshpass -p<pass> / -P <passfile> — catches the glued form the flag matcher misses.
_SSHPASS = re.compile(r"(\bsshpass\s+-[pP])\s*(\S+)")

# ssh-keygen passphrases: -N <new> / -P <old>
_KEYGEN_PASS = re.compile(
    r"(\bssh-keygen\b[^\n]*?\s-[NP])(\s*=?\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)

# echo/printf <secret> | ... --password-stdin  (secret lives in the pipe source)
_STDIN_PASS = re.compile(
    r"(\b(?:echo|printf)\s+)(\"[^\"]*\"|'[^']*'|[^|]+?)(\s*\|\s*[^\n]*--password-stdin)",
    re.IGNORECASE,
)

# High-entropy / well-known token shapes anywhere on the line.
_BLOBS = (
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),                 # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),                  # OpenAI-style
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                     # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
)


def redact(command: str) -> str:
    """Mask secrets in a command. Returns "" if the command must not be stored."""
    if not command:
        return command
    for pat in _DENYLIST:
        if pat.search(command):
            return ""
    out = _FLAG.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", command)
    out = _ENV.sub(lambda m: f"{m.group(1)}={MASK}", out)
    out = _AUTH.sub(lambda m: f"{m.group(1)}{MASK}", out)
    out = _URLCRED.sub(lambda m: f"{m.group(1)}{MASK}{m.group(2)}", out)
    out = _STDIN_PASS.sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", out)
    out = _SSHPASS.sub(lambda m: f"{m.group(1)} {MASK}", out)
    out = _KEYGEN_PASS.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    for pat in _BLOBS:
        out = pat.sub(MASK, out)
    return out
