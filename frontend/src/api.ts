export interface GitInfo { is_repo: boolean; branch: string | null; dirty: number; last_commit: string | null }
export interface RunInfo {
  active: boolean; kind: string | null; paused: boolean; task_id: string | null; root_task_id: string | null;
  pending_approvals: Approval[];
}
export interface Project {
  id: string; name: string; workspace_path: string; description: string; tech_stack: string;
  created_at: string; last_activity: string; workspace_exists: boolean;
  current_task: { id: string; title: string; status: string } | null;
  progress: { done: number; total: number }; git: Partial<GitInfo>; run: RunInfo;
}
export interface Approval { id: string; project_id: string; task_id: string | null; tool: string; arguments: Record<string, unknown>; reasons: string[]; created: string }
export interface ChatMessage { id: number; role: string; agent_role: string | null; content: string; task_id: string | null; created_at: string }
export interface TaskRow {
  id: string; project_id: string; parent_id: string | null; title: string; description: string; role: string; status: string;
  priority: number; position: number; depends_on: string[]; retries: number; steps_used: number; result: string; error: string;
  checkpoint_id: string | null; created_at: string; updated_at: string;
}
export interface ToolRunRow {
  id: number; task_id: string | null; tool: string; arguments: Record<string, unknown>; ok: boolean; status: string; summary: string;
  files_changed: string[]; duration_ms: number; approval: string; created_at: string;
}
export interface ForgeEvent { id?: number | null; type: string; data: any; task_id?: string | null; ts?: string; project_id?: string }
export interface MemoryItem { id: number; kind: string; title: string; content: string; pinned: boolean; updated_at: string }
export interface Checkpoint { id: string; commit: string; label: string; created_at: string }
export interface TestResult { id: number; kind: string; ok: boolean; summary: string; created_at: string; duration_ms: number; output_tail: string }
export interface FileEntry { name: string; path: string; is_dir: boolean; size: number | null }

const TOKEN_KEY = "forge_token";
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; query?: Record<string, string | number | undefined> } = {}): Promise<T> {
  const q = opts.query ? "?" + new URLSearchParams(Object.entries(opts.query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])) : "";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const t = getToken();
  if (t) headers["X-Forge-Token"] = t;
  let res: Response;
  try {
    res = await fetch(`/api${path}${q}`, { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  } catch (e) {
    throw new ApiError(0, "Cannot reach the ForgeAgent backend. Is it running on 127.0.0.1:8000?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const STATUS_STYLE: Record<string, string> = {
  PENDING: "bg-zinc-700/60 text-zinc-300", PLANNING: "bg-violet-500/20 text-violet-300", RUNNING: "bg-sky-500/20 text-sky-300",
  TESTING: "bg-amber-500/20 text-amber-300", BLOCKED: "bg-orange-500/20 text-orange-300", FAILED: "bg-red-500/20 text-red-300",
  COMPLETED: "bg-emerald-500/20 text-emerald-300", CANCELLED: "bg-zinc-600/40 text-zinc-400",
};

export function timeAgo(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
