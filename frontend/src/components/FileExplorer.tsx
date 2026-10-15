import { useCallback, useEffect, useState } from "react";
import { ChevronRight, ChevronDown, File, Folder, RefreshCw } from "lucide-react";
import { api, FileEntry } from "../api";

interface Props { pid: string; version: number; onOpen: (path: string) => void; selected: string | null }

function Node({ pid, entry, depth, version, onOpen, selected }: { pid: string; entry: FileEntry; depth: number; version: number; onOpen: (p: string) => void; selected: string | null }) {
  const [open, setOpen] = useState(false);
  const [kids, setKids] = useState<FileEntry[] | null>(null);
  const load = useCallback(() => api<FileEntry[]>(`/projects/${pid}/tree`, { query: { path: entry.path } }).then(setKids).catch(() => setKids([])), [pid, entry.path]);
  useEffect(() => { if (open) load(); }, [open, version, load]);
  const pad = { paddingLeft: 6 + depth * 12 };
  if (!entry.is_dir)
    return <div style={pad} onClick={() => onOpen(entry.path)} className={`flex items-center gap-1.5 py-[3px] cursor-pointer hover:bg-panel2 ${selected === entry.path ? "bg-accent/15 text-ink" : "text-dim"}`}><File size={13} className="shrink-0" /><span className="truncate">{entry.name}</span></div>;
  return (
    <div>
      <div style={pad} onClick={() => setOpen(!open)} className="flex items-center gap-1 py-[3px] cursor-pointer hover:bg-panel2">
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<Folder size={13} className="text-amber-300/80 shrink-0" /><span className="truncate">{entry.name}</span>
      </div>
      {open && kids?.map((k) => <Node key={k.path} pid={pid} entry={k} depth={depth + 1} version={version} onOpen={onOpen} selected={selected} />)}
    </div>
  );
}

export default function FileExplorer({ pid, version, onOpen, selected }: Props) {
  const [root, setRoot] = useState<FileEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<FileEntry[]>(`/projects/${pid}/tree`).then((r) => { setRoot(r); setErr(null); }).catch((e) => setErr(e.message)), [pid]);
  useEffect(() => { load(); }, [load, version]);
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-2 py-1.5 text-[11px] uppercase tracking-wide text-dim border-b border-line">Explorer<button onClick={load} title="Refresh"><RefreshCw size={12} /></button></div>
      <div className="flex-1 overflow-auto py-1 text-[12.5px]">
        {err && <div className="p-2 text-bad">{err}</div>}
        {root?.length === 0 && <div className="p-3 text-dim">Workspace is empty.</div>}
        {root?.map((e) => <Node key={e.path} pid={pid} entry={e} depth={0} version={version} onOpen={onOpen} selected={selected} />)}
      </div>
    </div>
  );
}
