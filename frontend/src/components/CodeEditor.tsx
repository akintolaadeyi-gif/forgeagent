import { useEffect, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { Save, X } from "lucide-react";
import { api } from "../api";

const LANG: Record<string, string> = { py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", json: "json", md: "markdown", html: "html", css: "css", yml: "yaml", yaml: "yaml", sql: "sql", sh: "shell", toml: "ini", rs: "rust", go: "go", java: "java", cs: "csharp", txt: "plaintext" };
const langOf = (p: string) => LANG[p.split(".").pop()?.toLowerCase() || ""] || "plaintext";

interface Props { pid: string; tabs: string[]; active: string | null; setActive: (p: string | null) => void; close: (p: string) => void; version: number; agentBusy: boolean }

export default function CodeEditor({ pid, tabs, active, setActive, close, version, agentBusy }: Props) {
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const dirty = useRef(false);
  dirty.current = content !== saved;

  useEffect(() => {
    if (!active) return;
    if (dirty.current) return; // never clobber unsaved edits by auto-refreshing
    api<{ content: string }>(`/projects/${pid}/file`, { query: { path: active } })
      .then((r) => { setContent(r.content); setSaved(r.content); setErr(null); })
      .catch((e) => { setErr(e.message); setContent(""); setSaved(""); });
  }, [pid, active, version]);

  const save = async () => {
    if (!active) return;
    try { await api(`/projects/${pid}/file`, { method: "PUT", body: { path: active, content } }); setSaved(content); setErr(null); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="h-full flex flex-col bg-bg">
      <div className="flex items-center border-b border-line bg-panel overflow-x-auto shrink-0">
        {tabs.map((t) => (
          <div key={t} onClick={() => { if (!dirty.current || t === active) setActive(t); }} className={`flex items-center gap-2 px-3 py-1.5 text-[12px] cursor-pointer border-r border-line whitespace-nowrap ${t === active ? "bg-bg text-ink border-t-2 border-t-accent" : "text-dim hover:text-ink"}`}>
            {t.split("/").pop()}{t === active && dirty.current ? " ●" : ""}
            <button onClick={(e) => { e.stopPropagation(); close(t); }}><X size={11} /></button>
          </div>
        ))}
        <span className="flex-1" />
        {active && <button className="btn mx-2 my-1" onClick={save} disabled={!dirty.current}><Save size={12} /> Save</button>}
      </div>
      {err && <div className="px-3 py-1 text-bad text-[12px]">{err}</div>}
      {agentBusy && active && <div className="px-3 py-0.5 text-[11px] text-amber-300 bg-amber-500/10">Agent is running — the file view refreshes automatically; your unsaved edits are never overwritten.</div>}
      <div className="flex-1 min-h-0">
        {active ? (
          <Editor theme="vs-dark" path={active} language={langOf(active)} value={content}
            onChange={(v) => setContent(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, automaticLayout: true, fontFamily: "JetBrains Mono, Cascadia Code, Consolas, monospace" }}
            onMount={(ed, monaco) => ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => document.getElementById("forge-save")?.click())} />
        ) : <div className="h-full flex items-center justify-center text-dim">Select a file from the explorer.</div>}
      </div>
      <button id="forge-save" className="hidden" onClick={save} />
    </div>
  );
}
