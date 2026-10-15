import { useEffect, useState } from "react";
import { FolderGit2, Plus, Trash2, GitBranch } from "lucide-react";
import { api, Project, STATUS_STYLE, timeAgo } from "../api";

export default function Dashboard({ open }: { open: (id: string) => void }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", workspace_path: "", description: "" });

  const load = () => api<Project[]>("/projects").then((p) => { setProjects(p); setErr(null); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); const t = setInterval(load, 6000); return () => clearInterval(t); }, []);

  const create = async () => {
    try {
      const p = await api<Project>("/projects", { body: { name: form.name, workspace_path: form.workspace_path || null, description: form.description } });
      setCreating(false); setForm({ name: "", workspace_path: "", description: "" });
      open(p.id);
    } catch (e: any) { setErr(e.message); }
  };
  const remove = async (p: Project) => {
    if (!confirm(`Remove "${p.name}" from ForgeAgent?\n\nThe files in ${p.workspace_path} are NOT deleted.`)) return;
    await api(`/projects/${p.id}`, { method: "DELETE" }); load();
  };

  return (
    <div className="h-full overflow-auto p-6">
      <div className="flex items-center justify-between mb-5">
        <div><h1 className="text-lg font-semibold">Projects</h1><p className="text-dim">Each project is an authorized workspace folder the agent may read and modify.</p></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={14} /> New project</button>
      </div>
      {err && <div className="card border-bad/50 text-bad p-3 mb-4">{err}</div>}

      {creating && (
        <div className="card p-4 mb-5 max-w-xl space-y-3">
          <div className="font-medium">Create project</div>
          <input className="w-full" placeholder="Project name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus />
          <input className="w-full mono" placeholder="Workspace folder (absolute path, optional — defaults to data/workspaces/<name>)" value={form.workspace_path} onChange={(e) => setForm({ ...form, workspace_path: e.target.value })} />
          <textarea className="w-full" rows={2} placeholder="Description (optional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <p className="text-dim text-[11px]">The folder is created if needed and initialised as a Git repository so checkpoints can be restored.</p>
          <div className="flex gap-2"><button className="btn btn-primary" disabled={!form.name.trim()} onClick={create}>Create &amp; open</button><button className="btn" onClick={() => setCreating(false)}>Cancel</button></div>
        </div>
      )}

      {projects && projects.length === 0 && !creating && (
        <div className="card p-8 text-center text-dim"><FolderGit2 className="mx-auto mb-2" />No projects yet. Create one to start building.</div>
      )}
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(360px,1fr))]">
        {projects?.map((p) => (
          <div key={p.id} className="card p-4 hover:border-accent/60 cursor-pointer" onClick={() => open(p.id)}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-semibold text-[14px] truncate">{p.name}</div>
                <div className="mono text-[11px] text-dim truncate" title={p.workspace_path}>{p.workspace_path}</div>
              </div>
              <button className="text-dim hover:text-bad" title="Remove from ForgeAgent" onClick={(e) => { e.stopPropagation(); remove(p); }}><Trash2 size={14} /></button>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-y-1.5 text-[12px]">
              <span className="text-dim">Stack</span><span className="truncate">{p.tech_stack || "—"}</span>
              <span className="text-dim">Current task</span>
              <span className="truncate flex items-center gap-1.5">{p.current_task ? <><span className={`chip ${STATUS_STYLE[p.current_task.status]}`}>{p.current_task.status}</span><span className="truncate">{p.current_task.title}</span></> : "—"}</span>
              <span className="text-dim">Last activity</span><span>{timeAgo(p.last_activity)}</span>
              <span className="text-dim">Git</span>
              <span className="flex items-center gap-1">{p.git?.is_repo ? <><GitBranch size={12} />{p.git.branch} · {p.git.dirty ? `${p.git.dirty} changed` : "clean"}</> : "not a repo"}</span>
            </div>
            <div className="mt-3">
              <div className="flex justify-between text-[11px] text-dim mb-1"><span>Completion</span><span>{p.progress.done}/{p.progress.total || 0}</span></div>
              <div className="h-1.5 rounded bg-panel2 overflow-hidden"><div className="h-full bg-good" style={{ width: `${p.progress.total ? (100 * p.progress.done) / p.progress.total : 0}%` }} /></div>
            </div>
            {p.run.active && <div className="mt-2 text-[11px] text-sky-300 flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-sky-400 pulse" />Agent working…</div>}
            {!p.workspace_exists && <div className="mt-2 text-[11px] text-bad">Workspace folder is missing on disk.</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
