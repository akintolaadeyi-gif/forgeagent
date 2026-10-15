# backend/app/security.py :: classify_command
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
            if not norm.startswith(ws):
                reasons.append(f"path outside workspace: {token}")
        else:
            reasons.append(f"path outside workspace: {token}")
    if not reasons:
        # every sub-command of a chain must start with an allowed prefix
        parts = [p.strip() for p in _CHAIN.split(low) if p and not _CHAIN.fullmatch(p) and p.strip()]
        prefixes = [p.lower() for p in autonomy.allowed_command_prefixes]
        for part in parts:
            part = _normalize_part(part)
            if not any(part == pre or part.startswith(pre + " ") or part.startswith(pre + ".exe") or
                       (" " in pre and part.startswith(pre)) for pre in prefixes):
                reasons.append(f"command not on the approved development list: '{part.split()[0] if part.split() else part}'")
                break
    return ("approval" if reasons else "auto"), reasons
