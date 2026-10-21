import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ConnectorPage } from "./pages/ConnectorPage";
import { DemoControl } from "./pages/DemoControl";
import { Evaluations } from "./pages/Evaluations";
import { IncidentDetail } from "./pages/IncidentDetail";
import { IncidentList } from "./pages/IncidentList";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="connectors/:id" element={<ConnectorPage />} />
        <Route path="incidents" element={<IncidentList />} />
        <Route path="incidents/:id" element={<IncidentDetail />} />
        <Route path="demo" element={<DemoControl />} />
        <Route path="evaluations" element={<Evaluations />} />
        <Route path="login" element={<Login />} />
        <Route path="*" element={<p>Không tìm thấy trang.</p>} />
      </Route>
    </Routes>
  );
}
