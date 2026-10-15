from app.llm.toolparse import extract_text_tool_calls, repair_json, split_thinking, normalize_native_calls

KNOWN = {"read_file", "create_file"}


def test_json_tool_call_block():
    calls, rest = extract_text_tool_calls('Sure.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>', KNOWN)
    assert calls[0].name == "read_file" and calls[0].arguments == {"path": "a.py"} and rest == "Sure."


def test_qwen_xml_dialect():
    txt = "<tool_call><function=create_file><parameter=path>x.txt</parameter><parameter=content>hi\nthere</parameter></function></tool_call>"
    calls, _ = extract_text_tool_calls(txt, KNOWN)
    assert calls[0].name == "create_file" and calls[0].arguments["content"] == "hi\nthere"


def test_fenced_json_and_unknown_tool():
    calls, _ = extract_text_tool_calls('```json\n{"name": "rm_rf", "arguments": {}}\n```', KNOWN)
    assert calls == []  # fenced JSON for unknown tools is not treated as a call
    calls, _ = extract_text_tool_calls('<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>', KNOWN)
    assert calls[0].error == "unknown tool 'rm_rf'"


def test_truncated_and_trailing_comma_repair():
    assert repair_json('{"a": 1, "b": [1,2,],}') == {"a": 1, "b": [1, 2]}
    assert repair_json('{"path": "a.py", "content": "abc') == {"path": "a.py", "content": "abc"}
    assert repair_json("{'a': True}") == {"a": True}


def test_unterminated_tool_call():
    calls, _ = extract_text_tool_calls('<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}', KNOWN)
    assert calls and calls[0].arguments == {"path": "a.py"}


def test_split_thinking():
    vis, th = split_thinking("<think>plan it</think>Answer")
    assert vis == "Answer" and th == "plan it"
    vis, th = split_thinking("Hello<think>never closed")
    assert vis == "Hello" and "never closed" in th


def test_native_bad_arguments_flagged():
    c = normalize_native_calls([{"id": "1", "function": {"name": "read_file", "arguments": "not json at all"}}])
    assert c[0].error
