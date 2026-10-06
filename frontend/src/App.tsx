// Routing.
//   Signed out: the public home page (/) and the sign-in page (/login).
//   Signed in:  the live stream opens once (LiveProvider) and every page
//               shares it; the home page stays reachable at /about.
import { Navigate, Route, Routes } from "react-router";
import { useSession } from "./lib/session";
import { LiveProvider } from "./lib/live";
import { useI18n } from "./lib/i18n";
import { Layout } from "./components/Layout";
import { LandingPage } from "./pages/Landing";
import { LoginPage } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { LivePage } from "./pages/Live";
import { SimulatorPage } from "./pages/Simulator";
import { SafetyPage } from "./pages/Safety";
import { ReportPage } from "./pages/Report";
import { PlannerPage } from "./pages/Planner";
import { UsersPage } from "./pages/Users";

export function App() {
  const { me, checked, can } = useSession();
  const { t } = useI18n();

  if (!checked) return <div className="login dim">{t("common.loading")}</div>;
  if (!me) {
    return (
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <LiveProvider>
      <Layout>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/simulator" element={<SimulatorPage />} />
          <Route path="/safety" element={<SafetyPage />} />
          <Route path="/report" element={<ReportPage />} />
          <Route path="/planner" element={<PlannerPage />} />
          <Route path="/about" element={<LandingPage embedded />} />
          {can("admin") && <Route path="/users" element={<UsersPage />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </LiveProvider>
  );
}
