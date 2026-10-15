import { useEffect, useState } from "react";
import { api } from "../api";

interface Health { connected: boolean; model: string | null; error: string | null; capabilities: Record<string, any> }
interface Res { cpu_pct: number; ram_total_gb: number; ram_used_gb: number; gpu: { name: string; vram_total_mb: number; vram_used_mb: number; util_pct: number } | null }
interface Stats { avg_tokens_per_second?: number | null; last_tokens_per_second?: number; requests: number }

export default function ModelBar() {
  const [h, setH] = useState<Health | null>(null);
  const [r, setR] = useState<Res | null>(null);
  const [s, setS] = useState<Stats | null>(null);
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [a, b, c] = await Promise.all([api<Health>("/model/status"), api<Res>("/system/resources"), api<Stats>("/model/stats")]);
        if (!alive) return;
        setH(a); setR(b); setS(c); setBackendDown(false);
      } catch { if (alive) setBackendDown(true); }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const dot = backendDown ? "bg-bad" : h?.connected ? "bg-good" : "bg-warn";
  const name = (h?.model || "").split(/[\\/]/).pop();
  return (
    <footer className="h-7 shrink-0 bg-panel border-t border-line flex items-center gap-4 px-3 text-[11px] text-dim overflow-hidden whitespace-nowrap">
      <span className="flex items-center gap-1.5"><span className={`w-2 h-2 rounded-full ${dot}`} />
        {backendDown ? "Backend offline" : h?.connected ? <span className="text-ink">{name}</span> : "Model server not reachable"}</span>
      {h?.connected && <span>ctx {h.capabilities.n_ctx ?? "?"}</span>}
      {s?.last_tokens_per_second ? <span>{s.last_tokens_per_second.toFixed(1)} tok/s{s.avg_tokens_per_second ? ` (avg ${s.avg_tokens_per_second})` : ""}</span> : null}
      <span className="flex-1" />
      {r && <span>CPU {r.cpu_pct.toFixed(0)}%</span>}
      {r && <span>RAM {r.ram_used_gb}/{r.ram_total_gb} GB</span>}
      {r?.gpu && <span>GPU {r.gpu.util_pct}% · VRAM {(r.gpu.vram_used_mb / 1024).toFixed(1)}/{(r.gpu.vram_total_mb / 1024).toFixed(0)} GB</span>}
    </footer>
  );
}
