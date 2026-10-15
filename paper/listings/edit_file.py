# backend/app/tools/fs_tools.py :: EditFile
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
