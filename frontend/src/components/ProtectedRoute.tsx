import React, { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ServerStartingCard } from "./ServerStartingCard";
import { useServerReady } from "../hooks/useServerReady";
import { fetchAuthStatus } from "../api";

type Props = {
  children: React.ReactNode;
};

export const ProtectedRoute: React.FC<Props> = ({ children }) => {
  const location = useLocation();
  const { ready: serverReady, timedOut: serverTimedOut } = useServerReady();
  const [checking, setChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    if (!serverReady) {
      setChecking(true);
      return;
    }

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
  }, [serverReady]);

  if (!serverReady || checking) {
    return (
      <ServerStartingCard
        title={
          serverReady
            ? "Checking session…"
            : "Waiting for API server to start…"
        }
        timedOut={serverTimedOut}
      />
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
};
