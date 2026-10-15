import { useState } from "react";
import { ArrowLeft, GitBranch, Gauge } from "lucide-react";
import { STATUS_STYLE } from "../api";
import { useProject } from "../useProject";
import ChatPanel from "../components/ChatPanel";
import FileExplorer from "../components/FileExplorer";
import CodeEditor from "../components/CodeEditor";
import { ActivityPanel, CheckpointPanel, MemoryPanel, TaskPanel, TestPanel, ToolHistory } from "../components/Panels";

const TABS = ["Activity", "Tasks", "Tools", "Tests", "Checkpoints", "Memory"] as const;

export default function Workspace({ pid }: { pid: string }) {
  const s = useProject(pid);
  const [tabs, setTabs] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [bottom, setBottom] = useState<(typeof TABS)[number]>("Activity");
  const open = (p: string) => { setTabs((t) => (t.includes(p) ? t : [...t, p])); setActive(p); };
  const close = (p: string) => { setTabs((t) => t.filter((x) => x !== p)); if (active === p) setActive(tabs.filter((x) => x !== p)[0] ?? null); };

  if (!s.project) return <div className="p-6 text-dim">{s.error ? <span className="text-bad">{s.error}</span> : "Loading project…"}</div>;
  const p = s.project;
  const active_ = !!s.run?.active;
  const current = s.tasks.filter((t) => !t.parent_id).slice(-1)[0];
  const kids = current ? s.tasks.filter((t) => t.parent_id === current.id) : [];
  const done = kids.filter((k) => k.status === "COMPLETED").length;
  const ctxPct = s.usage ? Math.min(100, Math.round((100 * s.usage.prompt_tokens) / s.usage.context_size)) : 0;

  return (
    <div className="h-full flex flex-col">
      <header className="h-10 shrink-0 bg-panel border-b border-line flex items-center gap-3 px-3">
        <button className="text-dim hover:text-ink" onClick={() => (location.hash = "#/projects")} title="All projects"><ArrowLeft size={16} /></button>
        <div className="font-semibold">{p.name}</div>
        <div className="mono text-[11px] text-dim truncate max-w-[40%]" title={p.workspace_path}>{p.workspace_path}</div>
        {p.git?.is_repo && <span className="text-[11px] text-dim flex items-center gap-1"><GitBranch size={12} />{p.git.branch} · {p.git.dirty ? `${p.git.dirty} changed` : "clean"}</span>}
        <span className="flex-1" />
        {current && <span className={`chip ${STATUS_STYLE[current.status]}`}>{current.status}</span>}
        {kids.length > 0 && <span className="text-[11px] text-dim">{done}/{kids.length} milestones</span>}
        {s.usage && <span className="text-[11px] text-dim flex items-center gap-1" title="Prompt tokens of the last request vs. context window"><Gauge size={12} />ctx {ctxPct}% · {s.usage.tokens_per_second || "—"} tok/s{s.usage.native_tools === false ? " · text tool-calls" : ""}</span>}
        <span title={s.connected ? "Live updates connected" : "Reconnecting…"} className={`w-2 h-2 rounded-full ${s.connected ? "bg-good" : "bg-warn pulse"}`} />
      </header>
      {s.error && <div className="bg-bad/15 text-bad px-3 py-1 text-[12px]">{s.error}</div>}

      <div className="flex-1 min-h-0 grid" style={{ gridTemplateColumns: "210px minmax(0,1fr) 400px" }}>
        <aside className="border-r border-line bg-panel min-h-0"><FileExplorer pid={pid} version={s.fileVersion} onOpen={open} selected={active} /></aside>
        <section className="min-w-0 min-h-0 flex flex-col">
          <div className="flex-[3] min-h-0 border-b border-line"><CodeEditor pid={pid} tabs={tabs} active={active} setActive={setActive} close={close} version={s.fileVersion} agentBusy={active_} /></div>
          <div className="flex-[2] min-h-0 flex flex-col bg-panel">
            <div className="flex border-b border-line shrink-0">
              {TABS.map((t) => (
                <button key={t} onClick={() => setBottom(t)} className={`px-3 py-1.5 text-[12px] border-b-2 ${bottom === t ? "border-accent text-ink" : "border-transparent text-dim hover:text-ink"}`}>
                  {t}{t === "Tests" && s.tests[0] ? <span className={s.tests[0].ok ? " text-good" : " text-bad"}> ●</span> : null}{t === "Tasks" && s.tasks.some((x) => x.status === "BLOCKED") ? <span className="text-orange-300"> !</span> : null}
                </button>))}
            </div>
            <div className="flex-1 min-h-0">
              {bottom === "Activity" && <ActivityPanel events={s.events} />}
              {bottom === "Tasks" && <TaskPanel tasks={s.tasks} pid={pid} active={active_} refresh={() => s.refresh()} />}
              {bottom === "Tools" && <ToolHistory runs={s.toolRuns} />}
              {bottom === "Tests" && <TestPanel tests={s.tests} />}
              {bottom === "Checkpoints" && <CheckpointPanel pid={pid} items={s.checkpoints} active={active_} refresh={() => s.refresh()} />}
              {bottom === "Memory" && <MemoryPanel pid={pid} items={s.memory} refresh={() => s.refresh(["memory"])} />}
            </div>
          </div>
        </section>
        <aside className="border-l border-line min-h-0">
          <ChatPanel project={p} messages={s.messages} live={s.live} thinking={s.thinking} approvals={s.approvals} run={s.run} refresh={() => s.refresh()} />
        </aside>
      </div>
    </div>
  );
}
