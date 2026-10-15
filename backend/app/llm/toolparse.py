"""Recovery of tool calls from model output that is not a well-formed native tool call.

Local models frequently emit tool calls as text (Qwen's <tool_call> JSON / XML dialects, fenced JSON) or
as slightly malformed JSON. This module never executes anything - it only extracts and repairs.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
_TOOL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FUNC_XML = re.compile(r"<function=([\w\-.]+)>\s*(.*?)\s*</function>", re.S)
_PARAM_XML = re.compile(r"<parameter=([\w\-.]+)>\s*(.*?)\s*</parameter>", re.S)
_FENCE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)


@dataclass
class ParsedCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])
    error: str | None = None  # set when arguments could not be parsed
    raw_arguments: str = ""

    def to_openai(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": json.dumps(self.arguments, ensure_ascii=False)}}


def split_thinking(text: str) -> tuple[str, str]:
    """Return (visible_text, reasoning). Handles an unterminated <think> block as well."""
    reasoning = "\n".join(m.strip() for m in THINK_RE.findall(text))
    visible = THINK_RE.sub("", text)
    if "<think>" in visible:  # unterminated
        head, _, tail = visible.partition("<think>")
        reasoning = (reasoning + "\n" + tail).strip()
        visible = head
    return visible.strip(), reasoning.strip()


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
    t = t.replace("“", '"').replace("”", '"')
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


def _coerce(value: str) -> Any:
    v = value.strip()
    if v[:1] in "[{" or v in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", v or "x"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return value
    return value.strip("\n")


def _from_obj(obj: Any) -> ParsedCall | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or (obj.get("function") or {}).get("name") if isinstance(obj.get("function"), dict) else obj.get("name")
    if not name and isinstance(obj.get("function"), str):
        name = obj["function"]
    if not name:
        return None
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(obj.get("function"), dict) and "arguments" in obj["function"]:
        args = obj["function"]["arguments"]
    raw = args if isinstance(args, str) else json.dumps(args)
    if isinstance(args, str):
        try:
            args = repair_json(args)
        except ValueError:
            return ParsedCall(name=str(name), arguments={}, error="arguments are not valid JSON", raw_arguments=raw)
    if not isinstance(args, dict):
        return ParsedCall(name=str(name), arguments={}, error="arguments must be a JSON object", raw_arguments=raw)
    return ParsedCall(name=str(name), arguments=args, raw_arguments=raw)


def extract_text_tool_calls(text: str, known_tools: set[str] | None = None) -> tuple[list[ParsedCall], str]:
    """Find tool calls embedded in assistant text. Returns (calls, text_without_call_blocks)."""
    calls: list[ParsedCall] = []
    remaining = text

    for m in _TOOL_BLOCK.finditer(text):
        body = m.group(1)
        fx = _FUNC_XML.search(body)
        if fx:  # Qwen3-Coder XML dialect
            args = {k: _coerce(v) for k, v in _PARAM_XML.findall(fx.group(2))}
            calls.append(ParsedCall(name=fx.group(1), arguments=args, raw_arguments=json.dumps(args)))
            continue
        try:
            c = _from_obj(repair_json(body))
        except ValueError:
            c = ParsedCall(name="", arguments={}, error="unparseable <tool_call> block", raw_arguments=body[:500])
        if c:
            calls.append(c)
    if calls:
        remaining = _TOOL_BLOCK.sub("", text)
    else:
        # unterminated <tool_call> (truncated)
        if "<tool_call>" in text:
            body = text.split("<tool_call>", 1)[1]
            fx = _FUNC_XML.search(body + "</function>") if "<function=" in body else None
            if fx:
                args = {k: _coerce(v) for k, v in _PARAM_XML.findall(fx.group(2))}
                calls.append(ParsedCall(name=fx.group(1), arguments=args, raw_arguments=json.dumps(args)))
            else:
                try:
                    c = _from_obj(repair_json(body))
                    if c:
                        calls.append(c)
                except ValueError:
                    pass
            if calls:
                remaining = text.split("<tool_call>", 1)[0]
        if not calls:
            for m in _FENCE.finditer(text):
                try:
                    c = _from_obj(repair_json(m.group(1)))
                except ValueError:
                    continue
                if c and (known_tools is None or c.name in known_tools):
                    calls.append(c)
            if calls:
                remaining = _FENCE.sub("", text)
    if known_tools is not None:
        for c in calls:
            if c.name and c.name not in known_tools and not c.error:
                c.error = f"unknown tool '{c.name}'"
    return calls, remaining.strip()


def normalize_native_calls(raw_calls: list[dict]) -> list[ParsedCall]:
    out: list[ParsedCall] = []
    for rc in raw_calls:
        fn = rc.get("function", {})
        name = fn.get("name", "") or ""
        raw = fn.get("arguments", "") or ""
        if isinstance(raw, dict):
            args, err = raw, None
        else:
            try:
                args, err = repair_json(raw) if raw.strip() else {}, None
            except ValueError:
                args, err = {}, "arguments are not valid JSON"
            if err is None and not isinstance(args, dict):
                args, err = {}, "arguments must be a JSON object"
        out.append(ParsedCall(name=name, arguments=args, id=rc.get("id") or ("call_" + uuid.uuid4().hex[:12]),
                              error=err, raw_arguments=raw if isinstance(raw, str) else json.dumps(raw)))
    return out
