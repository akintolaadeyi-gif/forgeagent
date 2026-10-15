import { useEffect, useState } from "react";
import { Boxes, Cpu, Settings as SettingsIcon, Hammer } from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Workspace from "./pages/Workspace";
import SettingsPage from "./pages/SettingsPage";
import ModelBar from "./components/ModelBar";

type View = { page: "projects" } | { page: "workspace"; id: string } | { page: "settings"; tab?: string };

function parseHash(): View {
  const h = location.hash.replace(/^#\/?/, "");
  if (h.startsWith("workspace/")) return { page: "workspace", id: h.split("/")[1] };
  if (h.startsWith("settings")) return { page: "settings", tab: h.split("/")[1] };
  return { page: "projects" };
}

export default function App() {
  const [view, setView] = useState<View>(parseHash);
  useEffect(() => {
    const on = () => setView(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (h: string) => { location.hash = h; };

  const NavBtn = ({ to, active, icon, label }: { to: string; active: boolean; icon: React.ReactNode; label: string }) => (
    <button onClick={() => go(to)} title={label}
      className={`flex flex-col items-center gap-1 w-14 py-2.5 text-[10px] border-l-2 ${active ? "border-accent text-ink bg-panel2" : "border-transparent text-dim hover:text-ink"}`}>
      {icon}{label}
    </button>
  );

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 flex min-h-0">
        <nav className="w-14 bg-panel border-r border-line flex flex-col items-center shrink-0">
          <div className="py-3 text-accent" title="ForgeAgent"><Hammer size={22} /></div>
          <NavBtn to="#/projects" active={view.page === "projects"} icon={<Boxes size={18} />} label="Projects" />
          <NavBtn to="#/settings/model" active={view.page === "settings"} icon={<SettingsIcon size={18} />} label="Settings" />
          {view.page === "workspace" && <NavBtn to={`#/workspace/${view.id}`} active icon={<Cpu size={18} />} label="Workspace" />}
        </nav>
        <main className="flex-1 min-w-0 min-h-0">
          {view.page === "projects" && <Dashboard open={(id) => go(`#/workspace/${id}`)} />}
          {view.page === "workspace" && <Workspace key={view.id} pid={view.id} />}
          {view.page === "settings" && <SettingsPage tab={view.tab || "model"} setTab={(t) => go(`#/settings/${t}`)} />}
        </main>
      </div>
      <ModelBar />
    </div>
  );
}
