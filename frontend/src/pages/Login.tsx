import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchAuthStatus, loginDashboard } from "../api";

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const from =
    (location.state as { from?: string } | null)?.from && (location.state as { from?: string }).from !== "/login"
      ? (location.state as { from?: string }).from!
      : "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await fetchAuthStatus();
        if (!cancelled && status.authenticated) {
          navigate(from, { replace: true });
        }
      } catch {
        // Stay on login page.
      } finally {
        if (!cancelled) {
          setChecking(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [from, navigate]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await loginDashboard(username.trim(), password);
      navigate(from, { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (checking) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="login-subtitle">Loading…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">IDP Dashboard</h1>
        <p className="login-subtitle">Sign in with your admin credentials to continue.</p>

        <form className="login-form" onSubmit={(e) => void handleSubmit(e)}>
          <label className="login-label" htmlFor="dashboard-username">
            Login ID
          </label>
          <input
            id="dashboard-username"
            className="login-input"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />

          <label className="login-label" htmlFor="dashboard-password">
            Password
          </label>
          <input
            id="dashboard-password"
            className="login-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          {error && <div className="alert alert-error">{error}</div>}

          <button type="submit" className="login-submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
};
