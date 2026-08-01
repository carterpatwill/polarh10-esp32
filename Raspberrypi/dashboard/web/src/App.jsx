import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home.jsx";
import SessionList from "./pages/SessionList.jsx";
import SessionDetail from "./pages/SessionDetail.jsx";
import Label from "./pages/Label.jsx";
import Morning from "./pages/Morning.jsx";
import Learn from "./pages/Learn.jsx";
import Tooltip from "./components/Tooltip.jsx";

// Hash routing (no server config needed — the Pi only ever serves "/"):
//   #/            today: readiness + workouts + calendar
//   #/day/:date   the same day view for any past date
//   #/sessions    flat workouts list (all days)
//   #/training    training-data list
//   #/session/:id detail
//   #/morning     morning HRV readiness trend + table
//   #/learn       HRV explainer / teaching page
//   #/label       labeler (Mac-only)
export default function App() {
  return (
    <HashRouter>
      <Tooltip />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/day/:date" element={<Home />} />
        <Route path="/sessions" element={<SessionList />} />
        <Route path="/training" element={<SessionList />} />
        <Route path="/session/:id" element={<SessionDetail />} />
        <Route path="/morning" element={<Morning />} />
        <Route path="/learn" element={<Learn />} />
        <Route path="/label" element={<div className="label-page"><Label /></div>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
