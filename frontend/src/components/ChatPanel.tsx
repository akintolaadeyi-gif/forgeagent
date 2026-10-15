import { useEffect, useRef, useState } from "react";
import { Send, Square, Pause, Play, ShieldAlert, Check, X, Brain } from "lucide-react";
import { api, Approval, ChatMessage, Project, RunInfo } from "../api";
import Markdown from "./Markdown";

interface Props {
  project: Project; messages: ChatMessage[]; live: { content: string; reasoning: string; role: string };
  thinking: { role: string; context_tokens: number; context_size: number } | null;
  approvals: Approval[]; run: RunInfo | null; refresh: () => void;
}

export default function ChatPanel({ project, messages, live, thinking, approvals, run, refresh }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"agent" | "chat">("agent");
  const [plan, setPlan] = useState<"auto" | "always" | "never">("auto");
  const [err, setErr] = useState<string | null>(null);
  const [showReasoning, setShowReasoning] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const active = !!run?.active;

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages.length, live.content, approvals.length, thinking]);

  const send = async () => {
    const message = text.trim();
    if (!message) return;
    setErr(null);
    try {
      await api(`/projects/${project.id}/chat`, { body: { message, mode, plan } });
      setText(""); refresh();
    } catch (e: any) { setErr(e.message); }
  };
  const act = (path: string) => api(`/projects/${project.id}/${path}`, { method: "POST" }).then(refresh).catch((e) => setErr(e.message));
  const decide = (a: Approval, approve: boolean) => api(`/approvals/${a.id}`, { body: { approve } }).catch((e) => setErr(e.message));

  return (
    <div className="h-full flex flex-col bg-panel">
      <div className="flex-1 overflow-auto p-3 space-y-3">
        {messages.length === 0 && !live.content && (
          <div className="text-dim text-center mt-10 px-6">
            <p className="mb-2 text-ink font-medium">Tell the agent what to build.</p>
            <p>e.g. “Create a Flask todo API with SQLite and tests” or “Fix the failing tests”. Use <b>Chat</b> mode for questions without tools.</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "ml-8" : "mr-4"}>
            <div className="text-[10px] uppercase tracking-wide text-dim mb-0.5">{m.role === "user" ? "You" : (m.agent_role || "agent")}</div>
            <div className={`rounded-lg px-3 py-2 ${m.role === "user" ? "bg-accent/15 border border-accent/30" : "bg-panel2 border border-line"}`}><Markdown text={m.content} /></div>
          </div>
        ))}
        {(live.content || live.reasoning) && (
          <div className="mr-4">
            <div className="text-[10px] uppercase tracking-wide text-dim mb-0.5 flex items-center gap-1.5">{live.role || "agent"} <span className="pulse text-sky-300">● live</span></div>
            {live.reasoning && !live.content && (
              <div className="rounded-lg px-3 py-2 bg-panel2/60 border border-dashed border-line text-dim text-[12px] whitespace-pre-wrap max-h-40 overflow-auto"><Brain size={12} className="inline mr-1" />{live.reasoning.slice(-700)}</div>
            )}
            {live.content && <div className="rounded-lg px-3 py-2 bg-panel2 border border-line whitespace-pre-wrap">{live.content}</div>}
          </div>
        )}
        {thinking && !live.content && !live.reasoning && (
          <div className="text-dim text-[12px] flex items-center gap-2"><span className="pulse">●</span> {thinking.role} is thinking… (context {thinking.context_tokens}/{thinking.context_size} tokens)</div>
        )}
        {approvals.map((a) => (
          <div key={a.id} className="card border-warn/60 p-3 mr-4">
            <div className="flex items-center gap-2 font-medium text-warn"><ShieldAlert size={15} /> Approval required: <span className="mono">{a.tool}</span></div>
            <ul className="text-[12px] text-dim list-disc ml-5 mt-1">{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            <pre className="mono text-[11px] bg-bg rounded p-2 mt-2 overflow-auto max-h-32 whitespace-pre-wrap">{JSON.stringify(a.arguments, null, 2)}</pre>
            <div className="flex gap-2 mt-2"><button className="btn btn-primary" onClick={() => decide(a, true)}><Check size={13} /> Approve</button><button className="btn btn-danger" onClick={() => decide(a, false)}><X size={13} /> Deny</button></div>
          </div>
        ))}
        <div ref={bottom} />
      </div>

      {err && <div className="px-3 py-1.5 text-bad text-[12px] border-t border-line">{err}</div>}
      <div className="border-t border-line p-2.5 space-y-2">
        {active && (
          <div className="flex items-center gap-2">
            <span className="text-sky-300 text-[12px] flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-sky-400 pulse" />{run?.paused ? "Paused" : "Working"}</span>
            <span className="flex-1" />
            {run?.paused ? <button className="btn" onClick={() => act("unpause")}><Play size={13} /> Resume</button> : <button className="btn" onClick={() => act("pause")}><Pause size={13} /> Pause</button>}
            <button className="btn btn-danger" onClick={() => act("stop")}><Square size={13} /> Stop</button>
          </div>
        )}
        <textarea className="w-full resize-none" rows={3} value={text} placeholder={active ? "Agent is running — stop or pause it to send a new request" : "Describe what you want built or fixed…  (Ctrl+Enter to send)"}
          onChange={(e) => setText(e.target.value)} disabled={active}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); }} />
        <div className="flex items-center gap-2">
          <div className="flex rounded-md overflow-hidden border border-line">
            {(["agent", "chat"] as const).map((m) => <button key={m} onClick={() => setMode(m)} className={`px-2.5 py-1 text-[12px] ${mode === m ? "bg-accent text-black font-semibold" : "bg-panel2 text-dim"}`}>{m === "agent" ? "Agent" : "Chat"}</button>)}
          </div>
          {mode === "agent" && (
            <select value={plan} onChange={(e) => setPlan(e.target.value as any)} title="Whether an Architect pass plans the work before implementation">
              <option value="auto">Plan: auto</option><option value="always">Plan: always</option><option value="never">Plan: never</option>
            </select>
          )}
          <span className="flex-1" />
          <button className="btn btn-primary" onClick={send} disabled={active || !text.trim()}><Send size={13} /> Send</button>
        </div>
      </div>
    </div>
  );
}
