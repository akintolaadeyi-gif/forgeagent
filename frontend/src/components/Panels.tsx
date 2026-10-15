import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, XCircle, RotateCcw, Play, Trash2, Plus, Pin, Search, Save, History } from "lucide-react";
import { api, Checkpoint, ForgeEvent, MemoryItem, STATUS_STYLE, TaskRow, TestResult, ToolRunRow, timeAgo } from "../api";

// ------------------------------------------------------------------------ activity timeline
const EVENT_LABEL: Record<string, string> = {
  tool_start: "▶", tool_end: "✔", task_status: "◆", milestone: "★", plan: "☰", error: "✖", warning: "⚠", retry: "↻",
  approval_required: "🔒", approval_resolved: "🔓", checkpoint: "⌑", context_compacted: "⇲", paused: "⏸", resumed: "⏵", run_finished: "■", user_message: "✎",
};

function describe(e: ForgeEvent): { text: string; tone: string } {
  const d = e.data || {};
  switch (e.type) {
    case "tool_start": return { text: `${d.tool}  ${Object.values(d.arguments || {})[0] ? String(Object.values(d.arguments)[0]).slice(0, 90) : ""}`, tone: "text-sky-300" };
    case "tool_end": return { text: `${d.tool}: ${d.summary || (d.ok ? "ok" : "failed")} (${d.duration_ms} ms)`, tone: d.ok ? "text-good" : "text-bad" };
    case "task_status": return { text: `${d.title?.slice(0, 70)} → ${d.status}${d.error ? " — " + String(d.error).slice(0, 80) : ""}`, tone: "text-violet-300" };
    case "milestone": return { text: d.text, tone: "text-amber-300" };
    case "plan": return { text: `Plan: ${(d.tasks || []).length} tasks`, tone: "text-amber-300" };
    case "error": case "warning": return { text: d.message, tone: e.type === "error" ? "text-bad" : "text-warn" };
    case "retry": return { text: `Retry #${d.attempt}: ${d.reason}`, tone: "text-warn" };
    case "approval_required": return { text: `Waiting for approval: ${d.tool}`, tone: "text-warn" };
    case "approval_resolved": return { text: `Approval ${d.result}`, tone: "text-dim" };
    case "checkpoint": return { text: `Git checkpoint ${d.commit}: ${d.label}`, tone: "text-good" };
    case "context_compacted": return { text: `Context compacted (was ~${d.before_tokens} tokens)`, tone: "text-dim" };
    case "assistant_message": return { text: `${d.role}: ${String(d.content || "").replace(/\s+/g, " ").slice(0, 110) || "(tool calls: " + (d.tools || []).join(", ") + ")"}`, tone: "text-ink/80" };
    case "run_finished": return { text: "Run finished", tone: "text-dim" };
    case "user_message": return { text: `Request: ${String(d.content).slice(0, 100)}`, tone: "text-ink" };
    default: return { text: e.type, tone: "text-dim" };
  }
}

export function ActivityPanel({ events }: { events: ForgeEvent[] }) {
  const end = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => events.filter((e) => e.type !== "tool_start" || true), [events]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [shown.length]);
  return (
    <div className="h-full overflow-auto p-2 mono text-[12px] space-y-0.5">
      {shown.length === 0 && <div className="text-dim p-2">No activity yet.</div>}
      {shown.map((e, i) => { const { text, tone } = describe(e); return (
        <div key={(e.id ?? "x") + "-" + i} className="flex gap-2"><span className="text-dim w-16 shrink-0">{e.ts ? new Date(e.ts).toLocaleTimeString() : ""}</span><span className="w-4 text-center">{EVENT_LABEL[e.type] || "·"}</span><span className={tone}>{text}</span></div>
      ); })}
      <div ref={end} />
    </div>
  );
}

// ------------------------------------------------------------------------ tool history + terminal output
export function ToolHistory({ runs }: { runs: ToolRunRow[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const [detail, setDetail] = useState<{ output: string } | null>(null);
  const toggle = async (id: number) => {
    if (open === id) { setOpen(null); return; }
    setOpen(id); setDetail(null);
    setDetail(await api(`/tool-runs/${id}`));
  };
  return (
    <div className="h-full overflow-auto text-[12px]">
      <table className="w-full"><thead className="sticky top-0 bg-panel text-dim text-left"><tr><th className="p-1.5 w-12">#</th><th>Tool</th><th>Summary</th><th>Approval</th><th className="text-right pr-2">Time</th></tr></thead>
        <tbody>
          {runs.map((r) => (<>
            <tr key={r.id} onClick={() => toggle(r.id)} className="cursor-pointer hover:bg-panel2 border-t border-line/50">
              <td className="p-1.5 text-dim">{r.id}</td>
              <td className="mono">{r.ok ? <CheckCircle2 size={12} className="inline text-good mr-1" /> : <XCircle size={12} className="inline text-bad mr-1" />}{r.tool}</td>
              <td className="truncate max-w-[420px]" title={r.summary}>{r.summary}</td>
              <td className={r.approval === "denied" ? "text-bad" : "text-dim"}>{r.approval}</td>
              <td className="text-right pr-2 text-dim">{r.duration_ms} ms</td>
            </tr>
            {open === r.id && <tr key={r.id + "d"}><td colSpan={5} className="bg-bg p-2"><div className="mono text-dim mb-1">{JSON.stringify(r.arguments).slice(0, 400)}</div><pre className="mono whitespace-pre-wrap max-h-64 overflow-auto">{detail ? detail.output || "(no output)" : "loading…"}</pre></td></tr>}
          </>))}
        </tbody></table>
      {runs.length === 0 && <div className="text-dim p-3">No tools have been executed yet.</div>}
    </div>
  );
}

// ------------------------------------------------------------------------ tests
export function TestPanel({ tests }: { tests: TestResult[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="h-full overflow-auto p-2 space-y-1.5 text-[12px]">
      {tests.length === 0 && <div className="text-dim p-2">No test or build runs yet.</div>}
      {tests.map((t) => (
        <div key={t.id} className="card p-2 cursor-pointer" onClick={() => setOpen(open === t.id ? null : t.id)}>
          <div className="flex items-center gap-2">{t.ok ? <CheckCircle2 size={14} className="text-good" /> : <XCircle size={14} className="text-bad" />}
            <span className="font-medium">{t.kind === "tests" ? "Tests" : "Build"} {t.ok ? "passed" : "failed"}</span><span className="text-dim">{t.summary}</span><span className="flex-1" /><span className="text-dim">{timeAgo(t.created_at)}</span></div>
          {open === t.id && <pre className="mono whitespace-pre-wrap mt-2 max-h-56 overflow-auto bg-bg p-2 rounded">{t.output_tail}</pre>}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------ tasks
export function TaskPanel({ tasks, pid, active, refresh }: { tasks: TaskRow[]; pid: string; active: boolean; refresh: () => void }) {
  const roots = tasks.filter((t) => !t.parent_id);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const call = (p: Promise<any>) => p.then(refresh).catch((e) => setErr(e.message));
  return (
    <div className="h-full overflow-auto p-2 text-[12px] space-y-3">
      {err && <div className="text-bad">{err}</div>}
      {roots.length === 0 && <div className="text-dim p-2">No tasks yet. Send a request to the agent, or queue one below.</div>}
      {roots.slice().reverse().map((r) => {
        const kids = tasks.filter((t) => t.parent_id === r.id).sort((a, b) => a.position - b.position);
        const done = kids.filter((k) => k.status === "COMPLETED").length;
        return (
          <div key={r.id} className="card p-2">
            <div className="flex items-center gap-2">
              <span className={`chip ${STATUS_STYLE[r.status]}`}>{r.status}</span><span className="font-medium truncate flex-1" title={r.description}>{r.title}</span>
              {["BLOCKED", "FAILED", "CANCELLED"].includes(r.status) && <button className="btn" disabled={active} onClick={() => call(api(`/tasks/${r.id}/resume`, { method: "POST" }))}><RotateCcw size={12} /> Resume</button>}
              {r.status === "PENDING" && <button className="btn btn-primary" disabled={active} onClick={() => call(api(`/tasks/${r.id}/run`, { method: "POST" }))}><Play size={12} /> Run</button>}
              {!["RUNNING", "PLANNING", "TESTING"].includes(r.status) && <button className="text-dim hover:text-bad" onClick={() => call(api(`/tasks/${r.id}`, { method: "DELETE" }))}><Trash2 size={12} /></button>}
            </div>
            {r.error && r.status !== "COMPLETED" && <div className="text-orange-300 mt-1">{r.error}</div>}
            {kids.length > 0 && <div className="mt-2"><div className="h-1.5 rounded bg-panel2 overflow-hidden"><div className="h-full bg-good" style={{ width: `${(100 * done) / kids.length}%` }} /></div><div className="text-dim mt-1">{done}/{kids.length} milestones</div></div>}
            <ul className="mt-1.5 space-y-1">{kids.map((k) => (
              <li key={k.id} className="flex items-center gap-2 pl-2 border-l-2 border-line">
                <span className={`chip ${STATUS_STYLE[k.status]}`}>{k.status}</span><span className="text-dim">{k.role}</span><span className="truncate flex-1" title={k.description}>{k.title}</span>
                <span className="text-dim">{k.steps_used} steps{k.retries ? ` · ${k.retries} retries` : ""}</span>
              </li>))}</ul>
          </div>
        );
      })}
      <div className="flex gap-2"><input className="flex-1" placeholder="Queue a task (run later)…" value={title} onChange={(e) => setTitle(e.target.value)} />
        <button className="btn" disabled={!title.trim()} onClick={() => call(api(`/projects/${pid}/tasks`, { body: { title } }).then(() => setTitle("")))}><Plus size={12} /> Queue</button></div>
    </div>
  );
}

// ------------------------------------------------------------------------ checkpoints
export function CheckpointPanel({ pid, items, active, refresh }: { pid: string; items: Checkpoint[]; active: boolean; refresh: () => void }) {
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const restore = async (c: Checkpoint) => {
    if (!confirm(`Restore workspace to "${c.label}"?\n\nThis discards all changes made after this checkpoint (uncommitted files are removed).`)) return;
    try { await api(`/projects/${pid}/checkpoints/${c.id}/restore`, { method: "POST" }); refresh(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="h-full overflow-auto p-2 text-[12px] space-y-1.5">
      {err && <div className="text-bad">{err}</div>}
      <div className="flex gap-2"><input className="flex-1" placeholder="Checkpoint label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <button className="btn" disabled={active} onClick={() => api(`/projects/${pid}/checkpoints`, { body: { label: label || "manual checkpoint" } }).then(() => { setLabel(""); refresh(); }).catch((e) => setErr(e.message))}><History size={12} /> Create</button></div>
      {items.map((c) => (<div key={c.id} className="card p-2 flex items-center gap-2"><span className="mono text-dim">{c.commit.slice(0, 8)}</span><span className="flex-1 truncate">{c.label}</span><span className="text-dim">{timeAgo(c.created_at)}</span>
        <button className="btn" disabled={active} onClick={() => restore(c)}><RotateCcw size={12} /> Restore</button></div>))}
      {items.length === 0 && <div className="text-dim p-2">No checkpoints yet. The agent creates one before each request and after each finished milestone.</div>}
    </div>
  );
}

// ------------------------------------------------------------------------ memory
const KINDS = ["requirement", "decision", "stack", "status", "completed", "pending", "error_fix", "file", "test", "convention", "note"];
export function MemoryPanel({ pid, items, refresh }: { pid: string; items: MemoryItem[]; refresh: () => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<MemoryItem[] | null>(null);
  const [edit, setEdit] = useState<Partial<MemoryItem> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!q.trim()) { setResults(null); return; } const t = setTimeout(() => api<MemoryItem[]>(`/projects/${pid}/memory`, { query: { q } }).then(setResults).catch(() => {}), 250); return () => clearTimeout(t); }, [q, pid]);
  const list = results ?? items;
  const save = async () => {
    if (!edit?.content) return;
    try {
      if (edit.id) await api(`/memory/${edit.id}`, { method: "PATCH", body: { kind: edit.kind, title: edit.title, content: edit.content, pinned: edit.pinned } });
      else await api(`/projects/${pid}/memory`, { body: { kind: edit.kind || "note", title: edit.title || "", content: edit.content, pinned: !!edit.pinned } });
      setEdit(null); refresh();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="h-full flex flex-col text-[12px]">
      <div className="flex gap-2 p-2 border-b border-line"><div className="relative flex-1"><Search size={12} className="absolute left-2 top-2.5 text-dim" /><input className="w-full pl-7" placeholder="Search memory (semantic + keyword)…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <button className="btn" onClick={() => setEdit({ kind: "note", content: "" })}><Plus size={12} /> Add</button></div>
      {err && <div className="px-2 text-bad">{err}</div>}
      {edit && (<div className="p-2 border-b border-line space-y-1.5 bg-panel2">
        <div className="flex gap-2"><select value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <input className="flex-1" placeholder="Title" value={edit.title || ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
          <label className="flex items-center gap-1 text-dim"><input type="checkbox" checked={!!edit.pinned} onChange={(e) => setEdit({ ...edit, pinned: e.target.checked })} />pinned</label></div>
        <textarea className="w-full" rows={3} value={edit.content} onChange={(e) => setEdit({ ...edit, content: e.target.value })} />
        <div className="flex gap-2"><button className="btn btn-primary" onClick={save}><Save size={12} /> Save</button><button className="btn" onClick={() => setEdit(null)}>Cancel</button></div></div>)}
      <div className="flex-1 overflow-auto p-2 space-y-1.5">
        {list.length === 0 && <div className="text-dim p-2">Nothing stored yet. The agent saves requirements, decisions, completed work and error fixes here automatically.</div>}
        {list.map((m) => (
          <div key={m.id} className="card p-2">
            <div className="flex items-center gap-2"><span className="chip bg-panel2 text-dim">{m.kind}</span>{m.pinned && <Pin size={11} className="text-amber-300" />}<span className="font-medium truncate flex-1">{m.title}</span>
              <button className="text-dim hover:text-ink" onClick={() => setEdit(m)}>edit</button>
              <button className="text-dim hover:text-bad" onClick={() => confirm("Delete this memory?") && api(`/memory/${m.id}`, { method: "DELETE" }).then(refresh)}><Trash2 size={12} /></button></div>
            <div className="text-dim whitespace-pre-wrap mt-1 line-clamp-4">{m.content}</div>
          </div>))}
      </div>
    </div>
  );
}
