"""Workspace boundary enforcement, command classification, secret redaction."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .app_settings import AutonomySettings


class WorkspaceViolation(Exception):
    pass


def resolve_in_workspace(root: str | Path, rel: str | Path, must_exist: bool = False) -> Path:
    """Resolve `rel` against the workspace root; refuse anything that escapes it (incl. symlinks / junctions)."""
    root_p = Path(root).resolve()
    raw = Path(str(rel))
    cand = raw if raw.is_absolute() else root_p / raw
    # realpath resolves symlinks for the existing part of the path
    resolved = Path(os.path.realpath(cand))
    try:
        resolved.relative_to(root_p)
    except ValueError:
        raise WorkspaceViolation(f"Path '{rel}' resolves outside the authorized workspace") from None
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path not found: {rel}")
    return resolved


def rel_display(root: str | Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix() or "."
    except ValueError:
        return str(p)


PROTECTED_DIRS = {".git", ".forge"}


def touches_protected(root: str | Path, p: Path) -> bool:
    try:
        parts = p.resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        return True
    return bool(parts) and parts[0] in PROTECTED_DIRS


# ---------------------------------------------------------------- secrets
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|passwd|password|authorization)(['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_\-\.=/+]{8,})"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    for pat in _SECRET_PATTERNS[1:]:
        out = pat.sub("[REDACTED]", out)
    return out


def redact_strong(text: str) -> str:
    """Only unmistakable credential formats (keys, tokens, private keys). Used on source-code reads so that
    ordinary `password = ...` assignments in code the agent is editing stay intact."""
    for pat in _SECRET_PATTERNS[1:]:
        text = pat.sub("[REDACTED]", text)
    return text


SECRET_FILE_PATTERNS =[re.compile(p, re.I) for p in (r"(^|/)\.env(\..+)?$", r"\.pem$", r"\.key$", r"id_rsa", r"\.pfx$", r"(^|/)credentials", r"\.kdbx$")]


def is_secret_file(rel_path: str) -> bool:
    rp = rel_path.replace("\\", "/")
    return any(p.search(rp) for p in SECRET_FILE_PATTERNS)


# ---------------------------------------------------------------- commands
_DESTRUCTIVE = [
    r"\brm\s+(-[a-z]*[rf][a-z]*)", r"\bdel\b", r"\brmdir\b", r"\brd\b\s", r"remove-item", r"\bformat\b\s+[a-z]:",
    r"\bgit\s+reset\s+--hard", r"\bgit\s+clean", r"\bgit\s+push", r"\bgit\s+checkout\s+--\s", r"\bgit\s+restore\b",
    r"\bshutdown\b", r"\btaskkill\b", r"\bstop-process\b", r"\breg(\.exe)?\s+(add|delete)", r"\bchmod\b", r"\bchown\b",
    r"\bnpm\s+publish", r"\bdocker\s+(push|system\s+prune|rm)", r"\bkubectl\b", r"\bterraform\s+(apply|destroy)",
    r"\bnet\s+user", r"\bsc\s+(delete|stop)", r"\bdrop\s+(table|database)\b", r"\bmkfs\b", r"\bdd\s+if=",
    r"\bset-executionpolicy", r"\binvoke-expression\b", r"\biex\b", r"\bcurl\b.*\|\s*(sh|bash|iex)",
]
_NETWORK = [r"\bcurl\b", r"\bwget\b", r"\binvoke-webrequest\b", r"\binvoke-restmethod\b", r"\biwr\b", r"\bscp\b", r"\bssh\b",
            r"\bftp\b", r"\bnc\b", r"\bgit\s+(clone|fetch|pull)\b", r"\bgit\s+remote\s+add"]
_DEPLOY = [r"\bdeploy\b", r"\bvercel\b", r"\bnetlify\b", r"\bfirebase\s+deploy", r"\bheroku\b", r"\baws\b", r"\baz\s", r"\bgcloud\b", r"\bnpm\s+publish", r"\btwine\s+upload"]
_SECRETS = [r"\.env\b", r"\bprintenv\b", r"\bget-childitem\s+env:", r"\benv:\w+", r"\bset\s*$", r"id_rsa", r"\.ssh", r"credential"]
_ABS_PATH = re.compile(r"(?<![\w/\\])([A-Za-z]:[\\/][^\s\"'|;&<>]*)|(?<![\w.])(\.\.[\\/])|(?<![\w:])(~[\\/])")
# POSIX absolute paths (/etc/passwd, "/tmp/x", --out=/var/y). Not matched when the slash continues a word, a URL
# ("https://x/y"), a relative path ("a/b", "./x") or a "~/" / "$VAR/" prefix. Only used on non-Windows hosts.
_POSIX_ABS = re.compile(r"(?<![\w.:/\\~$])(/[^\s\"'|;&<>()`]*)")
_SAFE_DEVICES = {"/dev/null", "/dev/stdout", "/dev/stderr"}
_CHAIN = re.compile(r"(\|\||&&|;|\||\n|`|\$\()")

def _inside_ws(norm: str, ws: str) -> bool:
    """True when `norm` is the workspace itself or lies below it, compared on a path-separator boundary
    (a plain startswith would also accept a sibling such as C:\\ws\\proj-other for C:\\ws\\proj)."""
    return norm == ws or norm.startswith(ws.rstrip("\\") + "\\")


def split_chain(cmd: str) -> list[str]:
    """Split a shell command into its sub-commands (; && || | newline), respecting quotes.

    Separators inside single quotes never split; inside double quotes only command substitution (`...` and $(...)) splits,
    because that really runs another command. Without this, `python -c "a; print(b)"` was cut at the `;` inside the string
    and the fragment `print(b)` was judged an unknown command."""
    parts: list[str] = []
    cur: list[str] = []
    quote = ""
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = ""
        elif quote == '"':
            if c == "\\" and i + 1 < n:
                cur.append(c + cmd[i + 1])
                i += 2
                continue
            if c == '"':
                quote = ""
                cur.append(c)
            elif c == "`" or cmd.startswith("$(", i):
                parts.append("".join(cur))
                cur = []
                i += 2 if c == "$" else 1
                continue
            else:
                cur.append(c)
        else:
            two = cmd[i:i + 2]
            if c in "'\"":
                quote = c
                cur.append(c)
            elif two in ("&&", "||"):
                parts.append("".join(cur))
                cur = []
                i += 2
                continue
            elif c in ";|\n`" or two == "$(":
                parts.append("".join(cur))
                cur = []
                if two == "$(":
                    i += 2
                    continue
            else:
                cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _normalize_part(part: str) -> str:
    """'"C:\\ws\\.venv\\Scripts\\python.exe" -m pytest' -> 'python -m pytest' (location is checked separately)."""
    part = part.strip().lstrip("&. ")
    m = re.match(r'^"([^"]+)"(.*)$', part) or re.match(r"^'([^']+)'(.*)$", part)
    head, rest = (m.group(1), m.group(2)) if m else (part.split(" ", 1)[0], part[len(part.split(" ", 1)[0]):])
    if "\\" in head or "/" in head:
        head = re.split(r"[\\/]", head)[-1]
        head = re.sub(r"\.(exe|cmd|bat)$", "", head)
    return (head + rest).strip()


_ANY = lambda pats, s: any(re.search(p, s, re.I) for p in pats)  # noqa: E731


def classify_command(cmd: str, workspace: str | Path, autonomy: AutonomySettings) -> tuple[str, list[str]]:
    """Return ("auto"|"approval", [reasons]). Never returns 'deny'; callers decide policy."""
    reasons: list[str] = []
    low = cmd.strip().lower()
    if _ANY(_DESTRUCTIVE, low):
        reasons.append("destructive operation")
    if _ANY(_NETWORK, low) or (not autonomy.network_access and re.search(r"https?://", low)):
        reasons.append("external/network access")
    if _ANY(_DEPLOY, low):
        reasons.append("deployment")
    if _ANY(_SECRETS, low):
        reasons.append("possible secrets access")
    ws = str(Path(workspace).resolve()).lower().replace("/", "\\")
    for m in _ABS_PATH.finditer(cmd):
        token = m.group(0)
        if m.group(1):
            norm = os.path.normpath(token).lower()
            if not _inside_ws(norm, ws):
                reasons.append(f"path outside workspace: {token}")
        else:
            reasons.append(f"path outside workspace: {token}")
    if os.name != "nt":
        ws_path = Path(workspace).resolve()
        for m in _POSIX_ABS.finditer(cmd):
            token = m.group(1)
            if token in _SAFE_DEVICES:
                continue
            try:
                Path(os.path.realpath(token)).relative_to(ws_path)
            except ValueError:
                reasons.append(f"path outside workspace: {token}")
    if not reasons:
        # every sub-command of a chain must start with an allowed prefix
        parts = [p.strip() for p in split_chain(low) if p.strip()]
        prefixes = [p.lower() for p in autonomy.allowed_command_prefixes]
        for part in parts:
            part = _normalize_part(part)
            if not any(part == pre or part.startswith(pre + " ") or part.startswith(pre + ".exe") or
                       (" " in pre and part.startswith(pre)) for pre in prefixes):
                reasons.append(f"command not on the approved development list: '{part.split()[0] if part.split() else part}'")
                break
    return ("approval" if reasons else "auto"), reasons
