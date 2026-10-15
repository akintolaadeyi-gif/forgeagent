"""Compare quantizations of the same model on speed, resource use, coding correctness and tool-call reliability.

Usage (run when nothing else is using the model server):
  backend\\.venv\\Scripts\\python scripts\\compare_models.py --label Q4_K_M --model C:\\AI\\Models\\Huihui27B\\...Q4_K_M.gguf
Results: data/compare_<label>.json  (summarised into docs/MODEL_COMPARISON.md by --report)
Coding tasks are scored by EXECUTING the generated code against hidden tests - no model-graded or eyeballed scoring.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SERVER = r"C:\AI\llama.cpp\llama-server.exe"
BASE = "http://127.0.0.1:8080"
PY = str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe")

# ------------------------------------------------------------------------------------------ coding tasks
TASKS = [
    ("is_palindrome", "Write a Python function `is_palindrome(s: str) -> bool` that returns True if s reads the same forwards and backwards, ignoring case and any non-alphanumeric characters.",
     "assert is_palindrome('A man, a plan, a canal: Panama')\nassert not is_palindrome('hello')\nassert is_palindrome('')\nassert is_palindrome('No lemon, no melon!')\nassert not is_palindrome('ab')"),
    ("merge_intervals", "Write a Python function `merge_intervals(intervals: list[list[int]]) -> list[list[int]]` that merges all overlapping (or touching) closed intervals and returns them sorted by start. The input may be unsorted.",
     "assert merge_intervals([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]\nassert merge_intervals([[1,4],[4,5]]) == [[1,5]]\nassert merge_intervals([]) == []\nassert merge_intervals([[5,6],[1,2],[2,3]]) == [[1,3],[5,6]]"),
    ("roman_to_int", "Write a Python function `roman_to_int(s: str) -> int` converting a Roman numeral (1..3999, standard subtractive notation) to an integer.",
     "assert roman_to_int('III') == 3\nassert roman_to_int('LVIII') == 58\nassert roman_to_int('MCMXCIV') == 1994\nassert roman_to_int('IX') == 9\nassert roman_to_int('MMMCMXCIX') == 3999"),
    ("flatten", "Write a Python function `flatten(x)` that takes an arbitrarily nested structure of lists/tuples containing ints and returns a flat list of the ints in order. Strings are not present.",
     "assert flatten([1,[2,[3,[4]]],5]) == [1,2,3,4,5]\nassert flatten([]) == []\nassert flatten([[],[[]]]) == []\nassert flatten((1,(2,[3]))) == [1,2,3]"),
    ("LRUCache", "Write a Python class `LRUCache` with `__init__(self, capacity: int)`, `get(self, key) -> int` (returns -1 if missing) and `put(self, key, value) -> None`. When capacity is exceeded evict the least recently used key; both get and put count as use. All operations must be O(1).",
     "c = LRUCache(2)\nc.put(1, 1); c.put(2, 2)\nassert c.get(1) == 1\nc.put(3, 3)\nassert c.get(2) == -1\nc.put(4, 4)\nassert c.get(1) == -1 and c.get(3) == 3 and c.get(4) == 4\nc2 = LRUCache(1); c2.put(1, 1); c2.put(1, 5); assert c2.get(1) == 5"),
    ("parse_csv_line", "Write a Python function `parse_csv_line(line: str) -> list[str]` that splits one CSV line on commas, supporting double-quoted fields that may contain commas and escaped quotes written as two double quotes (\"\"). Do not use the csv module.",
     "assert parse_csv_line('a,b,c') == ['a','b','c']\nassert parse_csv_line('a,\"b,c\",d') == ['a','b,c','d']\nassert parse_csv_line('\"he said \"\"hi\"\"\",x') == ['he said \"hi\"','x']\nassert parse_csv_line('') == ['']\nassert parse_csv_line('a,,b') == ['a','','b']"),
    ("top_k_frequent", "Write a Python function `top_k_frequent(words: list[str], k: int) -> list[str]` returning the k most frequent words, ordered by descending frequency; ties are broken alphabetically (ascending).",
     "assert top_k_frequent(['i','love','leetcode','i','love','coding'], 2) == ['i','love']\nassert top_k_frequent(['the','day','is','sunny','the','the','the','sunny','is','is'], 4) == ['the','is','sunny','day']\nassert top_k_frequent(['b','a'], 1) == ['a']"),
    ("valid_brackets", "Write a Python function `valid_brackets(s: str) -> bool` that returns True if every (), [] and {} in s is correctly nested and closed. Other characters are ignored.",
     "assert valid_brackets('()[]{}')\nassert valid_brackets('{[()]}')\nassert not valid_brackets('(]')\nassert not valid_brackets('((')\nassert valid_brackets('a(b)c')\nassert not valid_brackets(']')\nassert valid_brackets('')"),
]

TOOLS = [
    {"type": "function", "function": {"name": "write_file", "description": "Create a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout_s": {"type": "integer"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search the code base", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}},
]
TOOL_CASES = [
    ("Create a file named notes.txt containing exactly the text hello world.", "write_file", {"path": "notes.txt", "content": "hello world"}),
    ("Run the shell command: python --version", "run_command", {"command": "python --version"}),
    ("Search the code for the text TODO and return at most 5 results.", "search_code", {"query": "TODO", "max_results": 5}),
    ("Write the file src/app.py with the content print(1).", "write_file", {"path": "src/app.py", "content": "print(1)"}),
    ("Run `npm test` with a timeout of 120 seconds.", "run_command", {"command": "npm test", "timeout_s": 120}),
    ("Find occurrences of def main in the code, max 3 results.", "search_code", {"query": "def main", "max_results": 3}),
]


# ------------------------------------------------------------------------------------------ helpers
def gpu() -> dict:
    out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,power.draw", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    u, m, p = [float(x) for x in out.split(",")]
    return {"util": u, "vram_mb": m, "watts": p}


class Sampler(threading.Thread):
    """Samples GPU utilisation and llama-server CPU cores busy while a generation runs."""

    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.proc, self.stop, self.gpu_u, self.cpu = psutil.Process(pid), threading.Event(), [], []

    def run(self):
        last = sum(self.proc.cpu_times()[:2]); t = time.time()
        while not self.stop.wait(1.0):
            try:
                self.gpu_u.append(gpu()["util"])
                now = sum(self.proc.cpu_times()[:2]); t2 = time.time()
                self.cpu.append((now - last) / (t2 - t)); last, t = now, t2
            except Exception:
                pass


def chat(messages, tools=None, max_tokens=4096, temperature=0.2, timeout=1800):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
    if tools:
        body["tools"] = tools
    r = httpx.post(BASE + "/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def run_task(code: str, tests: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        Path(d, "t.py").write_text(code + "\n\n" + tests + "\nprint('ALL_OK')\n", encoding="utf-8")
        try:
            p = subprocess.run([PY, "t.py"], cwd=d, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return ("ALL_OK" in p.stdout), (p.stderr.strip().splitlines() or [""])[-1][:160]


def start_server(model: str, ctx: int, kv: str, ngl: int | None) -> subprocess.Popen:
    subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True)
    time.sleep(3)
    args = [SERVER, "-m", model, "-c", str(ctx), "--host", "127.0.0.1", "--port", "8080", "--jinja", "-fa", "on", "-np", "1"]
    if ngl is not None:
        args += ["-ngl", str(ngl)]
    if kv != "f16":
        args += ["-ctk", kv, "-ctv", kv]
    log = open(DATA / "llama_compare.log", "w", encoding="utf-8")
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(600):
        if p.poll() is not None:
            raise RuntimeError("llama-server exited; see data/llama_compare.log")
        try:
            if httpx.get(BASE + "/health", timeout=2).status_code == 200:
                return p
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("server did not become ready")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label"); ap.add_argument("--model")
    ap.add_argument("--ctx", type=int, default=65536); ap.add_argument("--kv", default="q8_0")
    ap.add_argument("--ngl", type=int, default=None, help="omit = llama.cpp auto-fit")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--skip-quality", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()

    t_load = time.time()
    srv = start_server(a.model, a.ctx, a.kv, a.ngl)
    res: dict = {"label": a.label, "model": Path(a.model).name, "size_gb": round(Path(a.model).stat().st_size / 2**30, 2),
                 "ctx": a.ctx, "kv": a.kv, "ngl": a.ngl, "load_s": round(time.time() - t_load, 1)}
    try:
        time.sleep(3)
        res["vram_idle_mb"] = gpu()["vram_mb"]
        # --- speed: short generation
        s = Sampler(srv.pid); s.start()
        r = chat([{"role": "user", "content": "Explain how a hash map works in about 300 words."}], max_tokens=400)
        s.stop.set(); s.join(2)
        t = r["timings"]
        res["gen_tps"] = round(t["predicted_per_second"], 2)
        res["gpu_util_avg"] = round(sum(s.gpu_u) / max(len(s.gpu_u), 1), 1)
        res["cpu_cores_busy"] = round(sum(s.cpu) / max(len(s.cpu), 1), 1)
        res["vram_busy_mb"] = gpu()["vram_mb"]
        # --- speed: long prompt processing (~8k tokens)
        text = ("def f(x):\n    return x * 2 + 1  # sample line for prompt processing\n" * 330)
        r = chat([{"role": "user", "content": text + "\nReply with one word: done"}], max_tokens=16)
        res["prompt_tokens_long"] = r["usage"]["prompt_tokens"]
        res["prompt_tps"] = round(r["timings"]["prompt_per_second"], 1)
        res["gen_tps_long_ctx"] = round(r["timings"].get("predicted_per_second", 0), 2)
        # --- tool calling
        ok = 0; details = []
        for prompt, name, args in TOOL_CASES:
            try:
                m = chat([{"role": "user", "content": prompt}], tools=TOOLS, max_tokens=1500)["choices"][0]["message"]
                tc = (m.get("tool_calls") or [None])[0]
                good = bool(tc) and tc["function"]["name"] == name and json.loads(tc["function"]["arguments"]) == args
            except Exception as e:
                good, tc = False, None
            ok += good; details.append(good)
        res["tool_call_ok"], res["tool_call_total"], res["tool_call_details"] = ok, len(TOOL_CASES), details
        # --- coding quality
        if not a.skip_quality:
            results, toks, secs = [], 0, 0.0
            for name, prompt, tests in TASKS:
                t0 = time.time()
                r = chat([{"role": "user", "content": prompt + "\nReply with only the code in one ```python block."}], max_tokens=6000)
                msg = r["choices"][0]["message"]["content"] or ""
                passed, err = run_task(extract_code(msg), tests)
                toks += r["usage"]["completion_tokens"]; secs += time.time() - t0
                results.append({"task": name, "pass": passed, "err": err, "completion_tokens": r["usage"]["completion_tokens"], "finish": r["choices"][0]["finish_reason"]})
                print(f"   {name:16s} {'PASS' if passed else 'FAIL'} {err}", flush=True)
            res["coding"] = results
            res["coding_pass"] = sum(x["pass"] for x in results)
            res["coding_total"] = len(results)
            res["coding_tokens_per_task"] = round(toks / len(results))
            res["coding_seconds_per_task"] = round(secs / len(results), 1)
    finally:
        subprocess.run(["taskkill", "/PID", str(srv.pid), "/T", "/F"], capture_output=True)
    (DATA / f"compare_{a.label}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("coding", "tool_call_details")}, indent=1))


def report() -> None:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("compare_*.json"))]
    order = {"Q4_K_M": 0, "IQ4_XS": 1, "IQ3_S": 2}
    rows.sort(key=lambda r: order.get(r["label"], 9))
    L = ["# Model comparison (measured)", "",
         "Same model family (Huihui-Qwen3.8-27B-abliterated), three quantizations, same machine (RTX 4090 Laptop 16 GB, 64 GB RAM), same prompts, "
         "context 65536, KV cache q8_0, llama.cpp b11620, llama.cpp auto GPU/CPU split. Coding tasks are scored by executing the generated code against hidden tests.", "",
         "| Quantization | File GB | Load s | VRAM busy MB | Gen tok/s | Prompt tok/s | GPU util % | CPU cores | Tool-call OK | Coding pass | Tokens/task |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['label']} | {r['size_gb']} | {r['load_s']} | {r['vram_busy_mb']:.0f} | {r['gen_tps']} | {r['prompt_tps']} | {r['gpu_util_avg']} | {r['cpu_cores_busy']} | "
                 f"{r['tool_call_ok']}/{r['tool_call_total']} | {r.get('coding_pass', '-')}/{r.get('coding_total', '-')} | {r.get('coding_tokens_per_task', '-')} |")
    L += ["", "## Per-task coding results", "", "| Task | " + " | ".join(r["label"] for r in rows) + " |", "|---|" + "---|" * len(rows)]
    for i, (name, _, _) in enumerate(TASKS):
        L.append(f"| {name} | " + " | ".join(("PASS" if r["coding"][i]["pass"] else "FAIL") if "coding" in r else "-" for r in rows) + " |")
    (ROOT / "docs" / "MODEL_COMPARISON.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
