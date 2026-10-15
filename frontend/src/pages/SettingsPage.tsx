import { useEffect, useState } from "react";
import { Save, Play, Square, Zap, RotateCcw } from "lucide-react";
import { api } from "../api";

type Settings = any;
const TABS = [["model", "Model & server"], ["agent", "Agent & prompts"], ["autonomy", "Autonomy & permissions"], ["memory", "Memory & context"]] as const;

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="block"><div className="text-[12px] font-medium mb-1">{label}</div>{children}{hint && <div className="text-[11px] text-dim mt-1">{hint}</div>}</label>;
}

export default function SettingsPage({ tab, setTab }: { tab: string; setTab: (t: string) => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [meta, setMeta] = useState<any>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { api("/settings").then(setS).catch((e) => setMsg({ ok: false, text: e.message })); api("/settings/meta").then(setMeta).catch(() => {}); }, []);
  if (!s) return <div className="p-6 text-dim">{msg ? <span className="text-bad">{msg.text}</span> : "Loading…"}</div>;

  const set = (path: string[], v: any) => {
    const next = structuredClone(s); let o = next; path.slice(0, -1).forEach((k) => (o = o[k])); o[path[path.length - 1]] = v;
    setS(next); setDirty(true); setMsg(null);
  };
  const save = async () => {
    try { setS(await api("/settings", { method: "PUT", body: s })); setDirty(false); setMsg({ ok: true, text: "Settings saved. They apply to the next agent step." }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const num = (path: string[], step = 1) => <input type="number" step={step} className="w-full" value={path.reduce((o: any, k) => o[k], s)} onChange={(e) => set(path, e.target.value === "" ? 0 : Number(e.target.value))} />;
  const txt = (path: string[], rows = 4) => <textarea className="w-full mono text-[12px]" rows={rows} value={path.reduce((o: any, k) => o[k], s)} onChange={(e) => set(path, e.target.value)} />;

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center border-b border-line bg-panel px-3 shrink-0">
        {TABS.map(([k, label]) => <button key={k} onClick={() => setTab(k)} className={`px-3 py-2.5 text-[12.5px] border-b-2 ${tab === k ? "border-accent text-ink" : "border-transparent text-dim hover:text-ink"}`}>{label}</button>)}
        <span className="flex-1" />
        {msg && <span className={`text-[12px] mr-3 ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</span>}
        <button className="btn" onClick={() => meta && confirm("Reset ALL settings to defaults?") && (setS(meta.defaults), setDirty(true))}><RotateCcw size={12} /> Defaults</button>
        <button className="btn btn-primary ml-2" disabled={!dirty} onClick={save}><Save size={12} /> Save</button>
      </div>
      <div className="flex-1 overflow-auto p-5">
        <div className="max-w-3xl space-y-5">
          {tab === "model" && <>
            <ServerPanel />
            <div className="card p-4 space-y-3">
              <div className="font-medium">Connection &amp; generation</div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="API endpoint (OpenAI-compatible)" hint="Keep this on 127.0.0.1 unless you deliberately expose the server."><input className="w-full mono" value={s.model.base_url} onChange={(e) => set(["model", "base_url"], e.target.value)} /></Field>
                <Field label="Model id" hint="Empty = auto-discover from /models."><input className="w-full mono" value={s.model.model} onChange={(e) => set(["model", "model"], e.target.value)} /></Field>
                <Field label="Context size (tokens)" hint="Must not exceed what llama-server was started with (-c). Drives the context budget & compaction."> {num(["model", "context_size"], 1024)}</Field>
                <Field label="Max output tokens per reply">{num(["model", "max_tokens"], 256)}</Field>
                <Field label="Temperature">{num(["model", "temperature"], 0.05)}</Field>
                <Field label="Top-p">{num(["model", "top_p"], 0.05)}</Field>
                <Field label="Top-k">{num(["model", "top_k"])}</Field>
                <Field label="Repeat penalty">{num(["model", "repeat_penalty"], 0.05)}</Field>
                <Field label="Request timeout (s)">{num(["model", "request_timeout_s"], 30)}</Field>
                <Field label="Retries on server errors">{num(["model", "max_retries"])}</Field>
              </div>
              <label className="flex items-center gap-2"><input type="checkbox" checked={s.model.native_tools} onChange={(e) => set(["model", "native_tools"], e.target.checked)} />Use native tool calling (automatically falls back to text-based tool calls if the server rejects it)</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={s.model.disable_thinking} onChange={(e) => set(["model", "disable_thinking"], e.target.checked)} />Append “/no_think” to requests (faster, for Qwen-style thinking models; may reduce quality on hard tasks)</label>
            </div>
          </>}

          {tab === "agent" && <div className="card p-4 space-y-4">
            <p className="text-dim">These texts are assembled into the system prompt. ForgeAgent adds no keyword filters; model refusals and execution permissions are separate mechanisms.</p>
            <Field label="Agent identity">{txt(["prompts", "identity"], 3)}</Field>
            <Field label="System instructions (operating rules)">{txt(["prompts", "system_instructions"], 8)}</Field>
            <Field label="Coding standards">{txt(["prompts", "coding_standards"], 5)}</Field>
            <Field label="Planning instructions (Architect role)">{txt(["prompts", "planning_instructions"], 4)}</Field>
            <Field label="Testing instructions">{txt(["prompts", "testing_instructions"], 4)}</Field>
          </div>}

          {tab === "autonomy" && <>
            <div className="card p-4 space-y-3">
              <div className="font-medium">Autonomy mode</div>
              {([["supervised", "Supervised", "Ask before every edit, command and external action. Reads are automatic."],
                 ["project_autonomous", "Project autonomous (recommended)", "Inspect, edit, build and test inside the workspace; approved dev commands run automatically. Destructive actions, secrets, deployments, network access and anything outside the workspace need approval."],
                 ["custom", "Custom", "Choose allow / ask / deny per tool below. Tools you leave unset follow the project-autonomous policy."]] as const).map(([v, t, d]) => (
                <label key={v} className={`flex gap-3 p-3 rounded-lg border cursor-pointer ${s.autonomy.mode === v ? "border-accent bg-accent/10" : "border-line"}`}>
                  <input type="radio" checked={s.autonomy.mode === v} onChange={() => set(["autonomy", "mode"], v)} /><div><div className="font-medium">{t}</div><div className="text-dim text-[12px]">{d}</div></div></label>))}
            </div>
            {s.autonomy.mode === "custom" && meta && (
              <div className="card p-4"><div className="font-medium mb-2">Per-tool permissions</div>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">{meta.tools.map((t: string) => (
                  <div key={t} className="flex items-center justify-between gap-2"><span className="mono text-[12px]">{t}</span>
                    <select value={s.autonomy.custom_permissions[t] || ""} onChange={(e) => { const cp = { ...s.autonomy.custom_permissions }; if (e.target.value) cp[t] = e.target.value; else delete cp[t]; set(["autonomy", "custom_permissions"], cp); }}>
                      <option value="">default policy</option><option value="allow">allow</option><option value="ask">ask</option><option value="deny">deny</option></select></div>))}</div></div>)}
            <div className="card p-4 space-y-3">
              <div className="font-medium">Limits</div>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Max steps per task" hint="Model calls before a task is parked as BLOCKED.">{num(["autonomy", "max_steps_per_task"])}</Field>
                <Field label="Auto-retries per task">{num(["autonomy", "max_retries_per_task"])}</Field>
                <Field label="Max consecutive tool errors">{num(["autonomy", "max_consecutive_errors"])}</Field>
                <Field label="Command timeout (s)">{num(["autonomy", "command_timeout_s"], 30)}</Field>
                <Field label="“Continue” nudges">{num(["autonomy", "max_nudges"])}</Field>
              </div>
              <label className="flex items-center gap-2"><input type="checkbox" checked={s.autonomy.review_pass} onChange={(e) => set(["autonomy", "review_pass"], e.target.checked)} />Planned runs end with a Reviewer pass (and one Debugger fix round if problems are found)</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={s.autonomy.network_access} onChange={(e) => set(["autonomy", "network_access"], e.target.checked)} />Treat network access as enabled for research (commands that use URLs still ask for approval)</label>
              <Field label="Commands that run without approval in autonomous mode (one prefix per line)">
                <textarea className="w-full mono text-[12px]" rows={7} value={s.autonomy.allowed_command_prefixes.join("\n")} onChange={(e) => set(["autonomy", "allowed_command_prefixes"], e.target.value.split("\n").map((x: string) => x.trim()).filter(Boolean))} /></Field>
            </div>
          </>}

          {tab === "memory" && <div className="card p-4 space-y-3">
            <label className="flex items-center gap-2"><input type="checkbox" checked={s.memory.enabled} onChange={(e) => set(["memory", "enabled"], e.target.checked)} />Enable project memory in prompts</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={s.memory.auto_extract} onChange={(e) => set(["memory", "auto_extract"], e.target.checked)} />Automatically record requirements, completed work and error fixes</label>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Memories retrieved per request">{num(["memory", "max_retrieved_items"])}</Field>
              <Field label="Compact history at (fraction of context)" hint="Older steps are summarised once the prompt reaches this share of the budget.">{num(["memory", "compaction_threshold"], 0.05)}</Field>
              <Field label="Recent messages kept verbatim">{num(["memory", "keep_recent_messages"])}</Field>
              <Field label="Tool output shown to model (chars)" hint="Full output stays available through read_full_output.">{num(["memory", "tool_output_max_chars"], 500)}</Field>
            </div>
          </div>}
        </div>
      </div>
    </div>
  );
}

function ServerPanel() {
  const [st, setSt] = useState<any>(null);
  const [files, setFiles] = useState<any>(null);
  const [log, setLog] = useState<string[]>([]);
  const [form, setForm] = useState({ model_path: "", context_size: 16384, gpu_layers: "" as string, kv_cache_type: "q8_0" });
  const [test, setTest] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => { api("/model/status").then(setSt).catch((e) => setErr(e.message)); api("/model/server/log").then((r: any) => setLog(r.log)).catch(() => {}); };
  useEffect(() => { load(); api("/model/files").then((f: any) => { setFiles(f); if (f.files[0]) setForm((x) => ({ ...x, model_path: x.model_path || (f.files.find((m: any) => /Q4_K_M/.test(m.name)) || f.files[0]).path })); }).catch(() => {}); const t = setInterval(load, 4000); return () => clearInterval(t); }, []);

  const start = async () => {
    setErr(null);
    try { await api("/model/server/start", { body: { model_path: form.model_path, context_size: form.context_size, gpu_layers: form.gpu_layers === "" ? null : Number(form.gpu_layers), kv_cache_type: form.kv_cache_type } }); load(); }
    catch (e: any) { setErr(e.message); }
  };
  const runTest = async () => { setTest("Testing…"); try { const r: any = await api("/model/test", { method: "POST" }); setTest(`Reply: “${r.reply.trim()}” · ${r.tokens_per_second} tok/s · ${r.duration_ms} ms`); } catch (e: any) { setTest("Failed: " + e.message); } };

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-3">
        <div className="font-medium">llama.cpp server</div>
        <span className={`chip ${st?.connected ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/20 text-red-300"}`}>{st?.connected ? "connected" : "not reachable"}</span>
        <span className="flex-1" /><button className="btn" onClick={runTest} disabled={!st?.connected}><Zap size={12} /> Test model</button>
      </div>
      {st?.connected && <div className="grid grid-cols-2 gap-y-1 text-[12px]">
        <span className="text-dim">Model</span><span className="mono truncate" title={st.model}>{String(st.model).split(/[\\/]/).pop()}</span>
        <span className="text-dim">Server context</span><span>{st.capabilities?.n_ctx ?? "?"} (trained {st.capabilities?.n_ctx_train ?? "?"})</span>
        <span className="text-dim">Parameters</span><span>{st.capabilities?.n_params ? (st.capabilities.n_params / 1e9).toFixed(1) + " B" : "?"} · {st.capabilities?.size_bytes ? (st.capabilities.size_bytes / 2 ** 30).toFixed(1) + " GiB" : ""}</span>
        <span className="text-dim">Chat template tools</span><span>{String(st.capabilities?.chat_template_supports_tools)}</span>
        <span className="text-dim">Build</span><span>{st.capabilities?.build_info}</span>
      </div>}
      {!st?.connected && st?.error && <div className="text-warn text-[12px]">{st.error}</div>}
      {test && <div className="text-[12px] text-dim">{test}</div>}
      {err && <div className="text-bad text-[12px]">{err}</div>}
      <div className="border-t border-line pt-3 space-y-2">
        <div className="text-[12px] text-dim">Start / switch the model (ForgeAgent can manage a llama-server it launches itself; an externally started server can simply be attached by endpoint).</div>
        {files && !files.llama_server_exists && <div className="text-warn text-[12px]">llama-server.exe not found at {files.llama_server}. See the installation guide.</div>}
        <div className="grid grid-cols-[1fr_110px_90px_90px] gap-2 items-end">
          <Field label="GGUF model"><select className="w-full" value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })}>{files?.files.map((m: any) => <option key={m.path} value={m.path}>{m.name} ({m.size_gb} GB)</option>)}</select></Field>
          <Field label="Context"><input type="number" className="w-full" value={form.context_size} onChange={(e) => setForm({ ...form, context_size: Number(e.target.value) })} /></Field>
          <Field label="GPU layers"><input className="w-full" placeholder="auto" value={form.gpu_layers} onChange={(e) => setForm({ ...form, gpu_layers: e.target.value })} /></Field>
          <Field label="KV cache"><select className="w-full" value={form.kv_cache_type} onChange={(e) => setForm({ ...form, kv_cache_type: e.target.value })}><option>f16</option><option>q8_0</option><option>q4_0</option></select></Field>
        </div>
        <div className="flex gap-2"><button className="btn btn-primary" onClick={start} disabled={!form.model_path || !!st?.server_process?.managed}><Play size={12} /> Start server</button>
          <button className="btn btn-danger" onClick={() => api("/model/server/stop", { method: "POST" }).then(load)} disabled={!st?.server_process?.managed}><Square size={12} /> Stop</button></div>
        {log.length > 0 && <pre className="mono text-[11px] bg-bg rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap">{log.slice(-25).join("\n")}</pre>}
      </div>
    </div>
  );
}
