import React, { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { fetchAuthStatus } from "../api";

type Props = {
  children: React.ReactNode;
};

export const ProtectedRoute: React.FC<Props> = ({ children }) => {
  const location = useLocation();
  const [checking, setChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await fetchAuthStatus();
        if (!cancelled) {
          setAuthenticated(status.authenticated);
        }
      } catch {
        if (!cancelled) {
          setAuthenticated(false);
        }
      } finally {
        if (!cancelled) {
          setChecking(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (checking) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="login-subtitle">Checking session…</p>
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
};
