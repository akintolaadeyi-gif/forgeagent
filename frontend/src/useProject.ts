import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken, Approval, ChatMessage, Checkpoint, ForgeEvent, MemoryItem, Project, RunInfo, TaskRow, TestResult, ToolRunRow } from "./api";

export interface Usage { prompt_tokens: number; completion_tokens: number; tokens_per_second: number; context_size: number; native_tools?: boolean }

export function useProject(pid: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [toolRuns, setToolRuns] = useState<ToolRunRow[]>([]);
  const [tests, setTests] = useState<TestResult[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [events, setEvents] = useState<ForgeEvent[]>([]);
  const [live, setLive] = useState({ content: "", reasoning: "", role: "" });
  const [thinking, setThinking] = useState<{ role: string; context_tokens: number; context_size: number } | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [fileVersion, setFileVersion] = useState(0);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async (which: string[] = ["project", "messages", "tasks", "toolRuns", "tests", "checkpoints"]) => {
    try {
      const jobs: Promise<void>[] = [];
      if (which.includes("project")) jobs.push(api<Project>(`/projects/${pid}`).then((p) => { setProject(p); setRun(p.run); setApprovals(p.run.pending_approvals); }));
      if (which.includes("messages")) jobs.push(api<ChatMessage[]>(`/projects/${pid}/messages`).then(setMessages));
      if (which.includes("tasks")) jobs.push(api<TaskRow[]>(`/projects/${pid}/tasks`).then(setTasks));
      if (which.includes("toolRuns")) jobs.push(api<ToolRunRow[]>(`/projects/${pid}/tool-runs`, { query: { limit: 150 } }).then(setToolRuns));
      if (which.includes("tests")) jobs.push(api<TestResult[]>(`/projects/${pid}/test-results`).then(setTests));
      if (which.includes("checkpoints")) jobs.push(api<Checkpoint[]>(`/projects/${pid}/checkpoints`).then(setCheckpoints));
      if (which.includes("memory")) jobs.push(api<MemoryItem[]>(`/projects/${pid}/memory`).then(setMemory));
      await Promise.all(jobs);
      setError(null);
    } catch (e: any) { setError(e.message); }
  }, [pid]);

  const debounced = useCallback((which?: string[]) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => refresh(which), 250);
  }, [refresh]);

  useEffect(() => {
    setEvents([]); setLive({ content: "", reasoning: "", role: "" }); setThinking(null);
    refresh(["project", "messages", "tasks", "toolRuns", "tests", "checkpoints", "memory"]);
    api<ForgeEvent[]>(`/projects/${pid}/events`, { query: { limit: 150 } }).then((evs) => setEvents(evs)).catch(() => {});

    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const tok = getToken();
      ws = new WebSocket(`${proto}://${location.host}/api/ws/projects/${pid}${tok ? `?token=${tok}` : ""}`);
      ws.onopen = () => { setConnected(true); retry = 0; refresh(); };
      ws.onclose = () => { setConnected(false); if (!closed) setTimeout(connect, Math.min(1000 * 2 ** retry++, 8000)); };
      ws.onmessage = (m) => {
        const ev: ForgeEvent = JSON.parse(m.data);
        if (ev.type === "ping") return;
        if (ev.type === "hello") { setRun(ev.data?.run ?? (ev as any).run); return; }
        handle(ev);
      };
    };
    const handle = (ev: ForgeEvent) => {
      const d = ev.data || {};
      switch (ev.type) {
        case "assistant_delta":
          setThinking(null);
          setLive((l) => d.kind === "reasoning" ? { ...l, reasoning: (l.reasoning + d.text).slice(-4000), role: d.role } : { ...l, content: l.content + d.text, role: d.role });
          return;
        case "thinking": setThinking(d); return;
        case "usage": setUsage(d); return;
        case "assistant_message": setLive({ content: "", reasoning: "", role: "" }); debounced(["messages"]); break;
        case "user_message": debounced(["messages", "project"]); break;
        case "tool_end": debounced(["toolRuns", "project"]); break;
        case "task_status": debounced(["tasks", "project"]); break;
        case "test_result": debounced(["tests"]); break;
        case "checkpoint": debounced(["checkpoints"]); break;
        case "files_changed": setFileVersion((v) => v + 1); return;
        case "approval_required": setApprovals((a) => a.some((x) => x.id === d.id) ? a : [...a, d]); break;
        case "approval_resolved": setApprovals((a) => a.filter((x) => x.id !== d.id)); break;
        case "paused": case "resumed": debounced(["project"]); break;
        case "run_finished": setLive({ content: "", reasoning: "", role: "" }); setThinking(null); debounced(["project", "messages", "tasks", "toolRuns", "tests", "checkpoints"]); setFileVersion((v) => v + 1); break;
        case "error": case "warning": break;
      }
      if (!["tool_start", "thinking"].includes(ev.type) || ev.type === "tool_start") setEvents((es) => [...es.slice(-400), ev]);
    };
    connect();
    const poll = window.setInterval(() => refresh(["project"]), 8000);
    return () => { closed = true; ws?.close(); window.clearInterval(poll); if (timer.current) window.clearTimeout(timer.current); };
  }, [pid, refresh, debounced]);

  return { project, messages, tasks, toolRuns, tests, checkpoints, memory, events, live, thinking, usage, approvals, run, fileVersion, connected, error, refresh };
}
