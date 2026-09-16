import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes, NavLink, useLocation } from "react-router-dom";
import { Home } from "./pages/Dashboard";
import { AnalyticsDashboard } from "./pages/AnalyticsDashboard";
import { Erp } from "./pages/Erp";
import { NotificationSettings } from "./pages/NotificationSettings";
import { Chat } from "./pages/Chat";
import { Settings } from "./pages/Settings";
import { LedgerSettings } from "./pages/LedgerSettings";
import { TallyMasterSettings } from "./pages/TallyMasterSettings";
import { CompanySettings } from "./pages/CompanySettings";
import { Health } from "./pages/Health";
import { Login } from "./pages/Login";
import { AccountMenu } from "./components/AccountMenu";
import { SettingsLayout } from "./components/SettingsLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { useCanonicalAppHost } from "./hooks/useCanonicalAppHost";
import amogaBrand from "./assets/amoga-brand-header.gif";
import "./styles.css";

const AppLayout: React.FC = () => {
  const location = useLocation();
  const isChatRoute = location.pathname === "/chat" || location.pathname.startsWith("/chat/");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <img className="app-brand-logo" src={amogaBrand} alt="Amoga Intelligent Machine Labs" />
          <h1>Intelligence Document Processing – Invoices</h1>
        </div>
        <div className="app-header-right">
          <nav className="app-nav">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/dashboard">Dashboard</NavLink>
            <NavLink to="/health">Health</NavLink>
            <NavLink to="/erp">ERP</NavLink>
            <NavLink to="/chat">Chatbot</NavLink>
          </nav>
          <AccountMenu />
        </div>
      </header>
      <main className={`app-main${isChatRoute ? " app-main--chat" : ""}`}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/dashboard" element={<AnalyticsDashboard />} />
          <Route path="/health" element={<Health />} />
          <Route path="/erp" element={<Erp />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<Settings />} />
            <Route path="notifications" element={<NotificationSettings />} />
            <Route path="company" element={<CompanySettings />} />
            <Route path="ledger" element={<LedgerSettings />} />
            <Route path="tally-masters" element={<TallyMasterSettings />} />
          </Route>
        </Routes>
      </main>
    </div>
  );
};

const App: React.FC = () => {
  useCanonicalAppHost();

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
};

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

