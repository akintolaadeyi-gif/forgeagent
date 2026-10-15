# backend/app/agent/permissions.py :: decide
def decide(tool: Tool, args: BaseModel, ctx: ToolContext) -> Decision:
    aut: AutonomySettings = ctx.settings.autonomy
    if tool.category == "meta":
        return Decision("allow")

    if aut.mode == "custom":
        pol = aut.custom_permissions.get(tool.name)
        if pol == "deny":
            return Decision("deny", [f"tool '{tool.name}' is disabled in custom permissions"])
        if pol == "ask":
            return Decision("ask", [f"approval required for '{tool.name}' (custom rule)"])
        if pol == "allow":
            return Decision("allow")
        # unspecified tools fall through to the project-autonomous policy

    if aut.mode == "supervised":
        if tool.category == "read":
            if tool.name == "read_file" and any(is_secret_file(p) for p in _paths_for(tool, args)):
                return Decision("ask", ["reads a file that may contain secrets"])
            return Decision("allow")
        if tool.category == "git" and not tool.mutates:
            return Decision("allow")
        return Decision("ask", [f"supervised mode: approval required for {tool.category} action '{tool.name}'"])

    # project_autonomous (and custom fall-through)
    reasons: list[str] = []
    if tool.category == "read":
        if tool.name == "read_file" and any(is_secret_file(p) for p in _paths_for(tool, args)):
            reasons.append("reads a file that may contain secrets")
        return Decision("ask" if reasons else "allow", reasons)
    if tool.category == "destructive":
        return Decision("ask", [f"destructive action: {tool.name}"])
    if tool.category == "write":
        if any(is_secret_file(p) for p in _paths_for(tool, args)):
            return Decision("ask", ["modifies a file that may contain secrets"])
        return Decision("allow")
    if tool.category == "exec":
        cmd = _command_for(tool, ctx, args)
        if cmd:
            verdict, why = classify_command(cmd, ctx.root, aut)
            if verdict == "approval":
                return Decision("ask", why)
        return Decision("allow")
    if tool.category == "browser":
        url = getattr(args, "url", None)
        if url and not is_local_url(url):
            return Decision("ask", [f"external URL: {url}"])
        return Decision("allow")
    return Decision("allow")
