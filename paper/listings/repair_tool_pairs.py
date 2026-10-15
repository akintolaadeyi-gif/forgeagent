# backend/app/agent/context.py :: repair_tool_pairs
def repair_tool_pairs(msgs: list[dict]) -> list[dict]:
    """Guarantee every assistant tool call is followed by a tool message (needed after interrupted runs)."""
    out: list[dict] = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        out.append(m)
        if m["role"] == "assistant" and m.get("tool_calls"):
            wanted = [tc["id"] for tc in m["tool_calls"]]
            j = i + 1
            seen: set[str] = set()
            while j < len(msgs) and msgs[j]["role"] == "tool":
                seen.add(msgs[j].get("tool_call_id", ""))
                out.append(msgs[j])
                j += 1
            for tc in m["tool_calls"]:
                if tc["id"] not in seen:
                    out.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                                "content": "ERROR: this action was interrupted (application restart or cancellation). Its outcome is unknown - re-inspect the current state before continuing."})
            i = j
            continue
        i += 1
    return out
