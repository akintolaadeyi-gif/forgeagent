"""Filesystem tools. Every path is validated against the authorized workspace."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ..security import is_secret_file, rel_display, resolve_in_workspace, touches_protected
from .base import Tool, ToolContext, ToolError, ToolResult, register
from .gitutil import git

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build", ".next",
               ".forge", ".mypy_cache", ".ruff_cache", "target", ".idea", ".vscode", "coverage", ".turbo"}
MAX_READ_BYTES = 2_000_000


def atomic_write(path: Path, data: str) -> None:
    """Write via temp file + os.replace so readers never see partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".forge-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolError(f"File too large ({path.stat().st_size} bytes). Use search_code or read a line range.")
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolError("File appears to be binary.")
    return raw.decode("utf-8", errors="replace")


def iter_files(root: Path, base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


def _safe(ctx: ToolContext, p: str, must_exist: bool = False) -> Path:
    return resolve_in_workspace(ctx.root, p, must_exist=must_exist)


def _writable(ctx: ToolContext, p: str) -> Path:
    path = _safe(ctx, p)
    if touches_protected(ctx.root, path):
        raise ToolError("Writing inside .git/ or .forge/ is not permitted.")
    return path


# --------------------------------------------------------------------------------------- list_directory
class ListDirParams(BaseModel):
    path: str = Field(".", description="Directory relative to the workspace root")
    depth: int = Field(2, ge=1, le=6, description="How many levels to show")


@register
class ListDirectory(Tool):
    name = "list_directory"
    description = "List files and folders (ignores node_modules, .git, build output). Use to explore the project."
    Params = ListDirParams
    category = "read"

    async def run(self, ctx, a: ListDirParams) -> ToolResult:
        base = _safe(ctx, a.path, must_exist=True)
        if not base.is_dir():
            raise ToolError(f"Not a directory: {a.path}")
        lines: list[str] = []
        count = 0

        def walk(d: Path, level: int):
            nonlocal count
            try:
                entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except OSError:
                return
            for e in entries:
                if e.name in IGNORE_DIRS:
                    continue
                count += 1
                if count > 400:
                    return
                indent = "  " * (level - 1)
                if e.is_dir():
                    lines.append(f"{indent}{e.name}/")
                    if level < a.depth:
                        walk(e, level + 1)
                else:
                    lines.append(f"{indent}{e.name}  ({e.stat().st_size} B)")

        walk(base, 1)
        if count > 400:
            lines.append("... (listing truncated at 400 entries)")
        out = "\n".join(lines) or "(empty directory)"
        return ToolResult(True, out, {"entries": count}, summary=f"listed {rel_display(ctx.root, base)} ({count} entries)", untrusted=True)


# --------------------------------------------------------------------------------------- read_file
class ReadFileParams(BaseModel):
    path: str
    start_line: int = Field(1, ge=1)
    end_line: int | None = Field(None, description="Inclusive; omit to read up to 400 lines from start_line")


@register
class ReadFile(Tool):
    name = "read_file"
    description = "Read a text file (with line numbers). Large files: pass start_line/end_line."
    Params = ReadFileParams
    category = "read"

    async def run(self, ctx, a: ReadFileParams) -> ToolResult:
        p = _safe(ctx, a.path, must_exist=True)
        if p.is_dir():
            raise ToolError("Path is a directory; use list_directory.")
        text = read_text(p)
        lines = text.splitlines()
        end = a.end_line or min(len(lines), a.start_line + 399)
        end = min(end, len(lines))
        if a.start_line > max(len(lines), 1):
            raise ToolError(f"start_line {a.start_line} beyond end of file ({len(lines)} lines)")
        body = "\n".join(f"{i + 1:>5}| {ln}" for i, ln in enumerate(lines[a.start_line - 1:end], start=a.start_line - 1))
        note = f"\n[showing lines {a.start_line}-{end} of {len(lines)}]" if end < len(lines) or a.start_line > 1 else ""
        return ToolResult(True, body + note, {"lines": len(lines), "path": rel_display(ctx.root, p)},
                          summary=f"read {rel_display(ctx.root, p)} ({len(lines)} lines)", untrusted=True)


# --------------------------------------------------------------------------------------- search_files
class SearchFilesParams(BaseModel):
    pattern: str = Field(description="Glob such as '*.py' or 'src/**/test_*.ts', or a substring of the file name")


@register
class SearchFiles(Tool):
    name = "search_files"
    description = "Find files by name/glob pattern."
    Params = SearchFilesParams
    category = "read"

    async def run(self, ctx, a: SearchFilesParams) -> ToolResult:
        pat = a.pattern
        hits = []
        for f in iter_files(ctx.root, ctx.root):
            rel = rel_display(ctx.root, f)
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat) or (not any(c in pat for c in "*?[") and pat.lower() in rel.lower()):
                hits.append(rel)
                if len(hits) >= 200:
                    break
        return ToolResult(True, "\n".join(hits) or "(no matches)", {"count": len(hits)}, summary=f"{len(hits)} files match '{pat}'", untrusted=True)


# --------------------------------------------------------------------------------------- search_code
class SearchCodeParams(BaseModel):
    query: str = Field(description="Regular expression (or plain text) to search for in file contents")
    path: str = Field(".", description="Subdirectory to limit the search")
    glob: str | None = Field(None, description="Only search files matching this glob, e.g. '*.ts'")
    case_sensitive: bool = False
    max_results: int = Field(60, ge=1, le=300)


@register
class SearchCode(Tool):
    name = "search_code"
    description = "Search file contents with a regex; returns path:line: text matches."
    Params = SearchCodeParams
    category = "read"

    async def run(self, ctx, a: SearchCodeParams) -> ToolResult:
        base = _safe(ctx, a.path, must_exist=True)
        try:
            rx = re.compile(a.query, 0 if a.case_sensitive else re.I)
        except re.error:
            rx = re.compile(re.escape(a.query), 0 if a.case_sensitive else re.I)
        out: list[str] = []
        for f in iter_files(ctx.root, base):
            if a.glob and not fnmatch.fnmatch(f.name, a.glob):
                continue
            try:
                if f.stat().st_size > 1_000_000:
                    continue
                raw = f.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:2048]:
                continue
            rel = rel_display(ctx.root, f)
            for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(out) >= a.max_results:
                        break
            if len(out) >= a.max_results:
                out.append("... (max_results reached)")
                break
        return ToolResult(True, "\n".join(out) or "(no matches)", {"count": len(out)}, summary=f"{len(out)} matches for '{a.query}'", untrusted=True)


# --------------------------------------------------------------------------------------- create_file
class CreateFileParams(BaseModel):
    path: str
    content: str
    overwrite: bool = Field(False, description="Set true to replace an existing file")


@register
class CreateFile(Tool):
    name = "create_file"
    description = "Create a new file with the given full content (parent folders are created). Use edit_file for changes to existing files."
    Params = CreateFileParams
    category = "write"
    mutates = True

    async def run(self, ctx, a: CreateFileParams) -> ToolResult:
        p = _writable(ctx, a.path)
        existed = p.exists()
        if existed and p.is_dir():
            raise ToolError("Path is a directory.")
        if existed and not a.overwrite:
            raise ToolError("File already exists. Use edit_file, or pass overwrite=true to replace it entirely.")
        if existed and read_text(p) == a.content:
            return ToolResult(True, "File already has exactly this content (no change made).", summary=f"unchanged {a.path}")
        async with ctx.services.lock(ctx.project_id, "fs"):
            atomic_write(p, a.content)
        rel = rel_display(ctx.root, p)
        return ToolResult(True, f"{'Overwrote' if existed else 'Created'} {rel} ({len(a.content.splitlines())} lines).",
                          files_changed=[rel], summary=f"{'overwrote' if existed else 'created'} {rel}")


# --------------------------------------------------------------------------------------- edit_file
class EditFileParams(BaseModel):
    path: str
    old_text: str = Field(description="Exact text to replace (must match once, including whitespace)")
    new_text: str
    replace_all: bool = False


@register
class EditFile(Tool):
    name = "edit_file"
    description = "Replace exact text in an existing file. old_text must be unique unless replace_all=true."
    Params = EditFileParams
    category = "write"
    mutates = True

    async def run(self, ctx, a: EditFileParams) -> ToolResult:
        p = _writable(ctx, a.path)
        if not p.is_file():
            raise ToolError(f"File not found: {a.path}")
        text = read_text(p)
        if not a.old_text:
            raise ToolError("old_text must not be empty")
        crlf = "\r\n" in text
        norm = text.replace("\r\n", "\n") if crlf else text
        old = a.old_text.replace("\r\n", "\n")
        new = a.new_text.replace("\r\n", "\n")
        n = norm.count(old)
        if n == 0:
            hint = _closest(norm, old)
            raise ToolError("old_text not found in file." + (f" Closest match in file:\n{hint}" if hint else ""))
        if n > 1 and not a.replace_all:
            raise ToolError(f"old_text matches {n} places; include more surrounding context or set replace_all=true.")
        updated = norm.replace(old, new) if a.replace_all else norm.replace(old, new, 1)
        if crlf:
            updated = updated.replace("\n", "\r\n")
        async with ctx.services.lock(ctx.project_id, "fs"):
            atomic_write(p, updated)
        rel = rel_display(ctx.root, p)
        diff = "".join(difflib.unified_diff(norm.splitlines(True), (updated.replace("\r\n", "\n")).splitlines(True), "a/" + rel, "b/" + rel, n=2))
        return ToolResult(True, f"Edited {rel} ({n if a.replace_all else 1} replacement(s)).\n{diff[:3000]}", files_changed=[rel], summary=f"edited {rel}")


def _closest(text: str, target: str) -> str:
    first = next((ln for ln in target.splitlines() if ln.strip()), "")
    if not first:
        return ""
    lines = text.splitlines()
    m = difflib.get_close_matches(first, lines, n=1, cutoff=0.6)
    if not m:
        return ""
    idx = lines.index(m[0])
    return "\n".join(f"{i + 1}| {lines[i]}" for i in range(max(0, idx - 1), min(len(lines), idx + 4)))


# --------------------------------------------------------------------------------------- apply_patch
class ApplyPatchParams(BaseModel):
    patch: str = Field(description="Unified diff (git style). Paths relative to workspace root, e.g. '--- a/src/x.py' / '+++ b/src/x.py'")


_PATCH_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


@register
class ApplyPatch(Tool):
    name = "apply_patch"
    description = "Apply a unified diff to one or more files (can also add/delete files)."
    Params = ApplyPatchParams
    category = "write"
    mutates = True

    async def run(self, ctx, a: ApplyPatchParams) -> ToolResult:
        paths = {m.group(1) for m in _PATCH_PATH.finditer(a.patch) if m.group(1) != "/dev/null"}
        if not paths:
            raise ToolError("No file paths found in patch; expected a unified diff with ---/+++ headers.")
        for pth in paths:
            _writable(ctx, pth)  # raises on workspace escape / protected dirs
        patch = a.patch if a.patch.endswith("\n") else a.patch + "\n"
        async with ctx.services.lock(ctx.project_id, "fs"):
            flags = ["--recount", "--whitespace=nowarn"]
            chk = await git(ctx.root, "apply", "--check", *flags, "-", stdin=patch)
            if chk.exit_code != 0:
                # models emit LF diffs; Windows files are often CRLF -> retry ignoring whitespace differences in context
                flags.append("--ignore-whitespace")
                chk = await git(ctx.root, "apply", "--check", *flags, "-", stdin=patch)
            if chk.exit_code != 0:
                raise ToolError("Patch does not apply cleanly:\n" + chk.output[:1500])
            r = await git(ctx.root, "apply", *flags, "-", stdin=patch)
            if r.exit_code != 0:
                raise ToolError("Patch failed:\n" + r.output[:1500])
        changed = sorted(paths)
        return ToolResult(True, f"Patch applied to: {', '.join(changed)}", files_changed=changed, summary=f"patched {len(changed)} file(s)")


# --------------------------------------------------------------------------------------- move_file
class MoveFileParams(BaseModel):
    source: str
    destination: str


@register
class MoveFile(Tool):
    name = "move_file"
    description = "Move or rename a file or directory inside the workspace."
    Params = MoveFileParams
    category = "write"
    mutates = True

    async def run(self, ctx, a: MoveFileParams) -> ToolResult:
        src = _writable(ctx, a.source)
        dst = _writable(ctx, a.destination)
        if not src.exists():
            raise ToolError(f"Source not found: {a.source}")
        if dst.exists():
            raise ToolError(f"Destination already exists: {a.destination}")
        async with ctx.services.lock(ctx.project_id, "fs"):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        s, d = rel_display(ctx.root, src), rel_display(ctx.root, dst)
        return ToolResult(True, f"Moved {s} -> {d}", files_changed=[s, d], summary=f"moved {s} -> {d}")


# --------------------------------------------------------------------------------------- delete_file
class DeleteFileParams(BaseModel):
    path: str
    recursive: bool = Field(False, description="Required to delete a non-empty directory")


@register
class DeleteFile(Tool):
    name = "delete_file"
    description = "Delete a file or directory inside the workspace (requires approval in most modes)."
    Params = DeleteFileParams
    category = "destructive"
    mutates = True

    async def run(self, ctx, a: DeleteFileParams) -> ToolResult:
        p = _writable(ctx, a.path)
        if p.resolve() == ctx.root.resolve():
            raise ToolError("Refusing to delete the workspace root.")
        if not p.exists():
            raise ToolError(f"Not found: {a.path}")
        async with ctx.services.lock(ctx.project_id, "fs"):
            if p.is_dir():
                if any(p.iterdir()) and not a.recursive:
                    raise ToolError("Directory is not empty; pass recursive=true.")
                shutil.rmtree(p)
            else:
                p.unlink()
        rel = rel_display(ctx.root, p)
        return ToolResult(True, f"Deleted {rel}", files_changed=[rel], summary=f"deleted {rel}")


__all__ = ["atomic_write", "is_secret_file"]
