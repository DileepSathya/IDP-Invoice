import React from "react";

type Props = {
  title?: string;
  timedOut?: boolean;
};

export const ServerStartingCard: React.FC<Props> = ({
  title = "Waiting for server…",
  timedOut = false,
}) => (
  <div className="login-page">
    <div className="login-card">
      <h1 className="login-title">IDP Dashboard</h1>
      {timedOut ? (
        <>
          <p className="login-subtitle">
            The API server is not responding yet. If you just restarted, wait a moment and
            refresh this page.
          </p>
          <button type="button" className="login-submit" onClick={() => window.location.reload()}>
            Retry
          </button>
        </>
      ) : (
        <p className="login-subtitle">{title}</p>
      )}
    </div>
  </div>
);
