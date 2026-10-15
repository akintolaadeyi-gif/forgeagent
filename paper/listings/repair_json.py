# backend/app/llm/toolparse.py :: repair_json
def repair_json(s: str) -> Any:
    """Best-effort repair of near-JSON produced by small models. Raises ValueError if hopeless."""
    s = s.strip()
    if not s:
        raise ValueError("empty")
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    t = s
    t = re.sub(r"^```(?:json)?|```$", "", t.strip()).strip()
    t = re.sub(r",\s*([}\]])", r"\1", t)  # trailing commas
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    # python-style literals
    t = re.sub(r"\bNone\b", "null", t)
    t = re.sub(r"\bTrue\b", "true", t)
    t = re.sub(r"\bFalse\b", "false", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # close unbalanced braces / brackets / strings (truncated output)
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
    fixed = t + ('"' if in_str else "") + "".join(reversed(stack))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # single-quoted JSON
    try:
        import ast

        v = ast.literal_eval(s)
        if isinstance(v, (dict, list)):
            return v
    except Exception:
        pass
    raise ValueError("could not repair JSON")
