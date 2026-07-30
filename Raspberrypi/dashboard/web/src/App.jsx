import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import SessionList from "./pages/SessionList.jsx";
import SessionDetail from "./pages/SessionDetail.jsx";
import Label from "./pages/Label.jsx";
import Tooltip from "./components/Tooltip.jsx";

// Hash routing (no server config needed — the Pi only ever serves "/"):
//   #/            workouts list
//   #/training    training-data list
//   #/session/:id detail
//   #/label       labeler (Mac-only)
export default function App() {
  return (
    <HashRouter>
      <Tooltip />
      <Routes>
        <Route path="/" element={<SessionList />} />
        <Route path="/training" element={<SessionList />} />
        <Route path="/session/:id" element={<SessionDetail />} />
        <Route path="/label" element={<div className="label-page"><Label /></div>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
