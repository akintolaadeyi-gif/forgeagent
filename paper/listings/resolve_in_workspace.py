# backend/app/security.py :: resolve_in_workspace
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
