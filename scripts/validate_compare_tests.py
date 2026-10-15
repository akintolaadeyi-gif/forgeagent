"""Checks that the hidden tests in compare_models.py accept a correct reference solution for every task,
so that a model's FAIL means the model is wrong, not the test."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cm", Path(__file__).with_name("compare_models.py"))
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

REF = {
    "is_palindrome": "def is_palindrome(s):\n    t=[c.lower() for c in s if c.isalnum()]\n    return t==t[::-1]",
    "merge_intervals": "def merge_intervals(iv):\n    out=[]\n    for a,b in sorted(iv):\n        if out and a<=out[-1][1]: out[-1][1]=max(out[-1][1],b)\n        else: out.append([a,b])\n    return out",
    "roman_to_int": "def roman_to_int(s):\n    v={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n    t=0\n    for i,c in enumerate(s):\n        t+= -v[c] if i+1<len(s) and v[c]<v[s[i+1]] else v[c]\n    return t",
    "flatten": "def flatten(x):\n    o=[]\n    for i in x:\n        o+= flatten(i) if isinstance(i,(list,tuple)) else [i]\n    return o",
    "LRUCache": "from collections import OrderedDict\nclass LRUCache:\n    def __init__(s,c): s.c=c; s.d=OrderedDict()\n    def get(s,k):\n        if k not in s.d: return -1\n        s.d.move_to_end(k); return s.d[k]\n    def put(s,k,v):\n        s.d[k]=v; s.d.move_to_end(k)\n        if len(s.d)>s.c: s.d.popitem(last=False)",
    "parse_csv_line": "def parse_csv_line(line):\n    out=[];cur='';q=False;i=0\n    while i<len(line):\n        c=line[i]\n        if q:\n            if c=='\"':\n                if i+1<len(line) and line[i+1]=='\"': cur+='\"'; i+=1\n                else: q=False\n            else: cur+=c\n        elif c=='\"': q=True\n        elif c==',': out.append(cur); cur=''\n        else: cur+=c\n        i+=1\n    out.append(cur); return out",
    "top_k_frequent": "from collections import Counter\ndef top_k_frequent(w,k):\n    c=Counter(w); return sorted(c,key=lambda x:(-c[x],x))[:k]",
    "valid_brackets": "def valid_brackets(s):\n    st=[];m={')':'(',']':'[','}':'{'}\n    for c in s:\n        if c in '([{': st.append(c)\n        elif c in m:\n            if not st or st.pop()!=m[c]: return False\n    return not st",
}
bad = []
for name, _, tests in cm.TASKS:
    ok, err = cm.run_task(REF[name], tests)
    print(f"{name:16s} reference {'passes' if ok else 'FAILS ' + err}")
    if not ok:
        bad.append(name)
# and a deliberately wrong solution must fail
ok, _ = cm.run_task("def is_palindrome(s):\n    return s == s[::-1]", cm.TASKS[0][2])
print("naive (wrong) solution rejected:", not ok)
raise SystemExit(1 if bad or ok else 0)
