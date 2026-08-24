import { NavLink, Route, Routes } from "react-router-dom";
import { Settings, Table2 } from "lucide-react";
import { Brand } from "./components/Brand";
import { HomePage } from "./pages/HomePage";
import { AnswerPage } from "./pages/AnswerPage";
import { TracesPage } from "./pages/TracesPage";
import { TraceDetailPage } from "./pages/TraceDetailPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink to="/" className="brand-link"><Brand /></NavLink>
        <nav aria-label="Primary navigation">
          <NavLink to="/traces"><Table2 size={15} /> Traces</NavLink>
          <NavLink to="/settings"><Settings size={15} /> Settings</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/answers/:runId" element={<AnswerPage />} />
          <Route path="/traces" element={<TracesPage />} />
          <Route path="/traces/:traceId" element={<TraceDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
