import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes, NavLink } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Chat } from "./pages/Chat";
import { Settings } from "./pages/Settings";
import "./styles.css";

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="app-header">
          <h1>Intelligence Document Processing – Invoices</h1>
          <nav className="app-nav">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/chat">Chatbot</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </header>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
};

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

