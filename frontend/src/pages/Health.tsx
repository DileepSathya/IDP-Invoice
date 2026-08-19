import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchSystemHealth, type HealthItem, type SystemHealth } from "../api";

const STATUS_LABELS: Record<HealthItem["status"], string> = {
  ok: "OK",
  warning: "Warning",
  error: "Error",
  info: "Info",
  disabled: "Off",
};

function overallLabel(overall: SystemHealth["overall"]): string {
  switch (overall) {
    case "ok":
      return "All critical checks passed";
    case "warning":
      return "Some items need attention";
    case "error":
      return "Critical issues detected";
    default:
      return overall;
  }
}

function HealthStatusBadge({ status }: { status: HealthItem["status"] }) {
  return <span className={`health-badge health-badge--${status}`}>{STATUS_LABELS[status]}</span>;
}

function HealthItemRow({ item }: { item: HealthItem }) {
  return (
    <div className="health-item">
      <div className="health-item-header">
        <span className="health-item-label">{item.label}</span>
        <HealthStatusBadge status={item.status} />
      </div>
      <p className="health-item-message">{item.message}</p>
      {item.fix_hint && <p className="health-item-hint">{item.fix_hint}</p>}
      {item.fix_route && (
        <Link to={item.fix_route} className="health-item-link">
          Open related settings
        </Link>
      )}
    </div>
  );
}

export const Health: React.FC = () => {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const data = await fetchSystemHealth();
        if (!cancelled) {
          setHealth(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load health status");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const intervalId = window.setInterval(() => {
      void fetchSystemHealth()
        .then((data) => {
          if (!cancelled) {
            setHealth(data);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : "Failed to refresh health status");
          }
        });
    }, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  const checkedAt = health?.checked_at ? new Date(health.checked_at).toLocaleString() : null;

  return (
    <div className="panel health-page">
      <div className="panel-header">
        <div>
          <h2>Health</h2>
          <p>Configuration, services, and runtime alerts. Refreshes every 30 seconds.</p>
        </div>
        {checkedAt && <span className="analytics-updated">Checked {checkedAt}</span>}
      </div>

      {loading && !health && <p className="status-text">Loading health checks…</p>}
      {error && <div className="alert alert-error">{error}</div>}

      {health && (
        <>
          <div className={`health-summary health-summary--${health.overall}`}>
            <div>
              <h3 className="health-summary-title">{overallLabel(health.overall)}</h3>
              <p className="health-summary-meta">
                {health.summary.error} error(s) · {health.summary.warning} warning(s) ·{" "}
                {health.summary.ok} OK
              </p>
            </div>
            <HealthStatusBadge status={health.overall === "ok" ? "ok" : health.overall} />
          </div>

          {health.critical_messages.length > 0 && (
            <div className="alert alert-error health-critical-list">
              {health.critical_messages.map((message) => (
                <div key={message}>{message}</div>
              ))}
            </div>
          )}

          {health.sections.map((section) => (
            <section key={section.id} className="health-section">
              <div className="health-section-header">
                <h3>{section.title}</h3>
                <HealthStatusBadge status={section.status} />
              </div>
              <div className="health-item-grid">
                {section.items.map((item) => (
                  <HealthItemRow key={item.id} item={item} />
                ))}
              </div>
            </section>
          ))}

          <section className="health-section">
            <div className="health-section-header">
              <h3>Recent log issues</h3>
            </div>
            {health.recent_issues.length === 0 ? (
              <p className="health-empty">No recent WARNING or ERROR lines in idp.log.</p>
            ) : (
              <ul className="health-log-list">
                {health.recent_issues.map((issue, index) => (
                  <li
                    key={`${issue.time}-${index}`}
                    className={`health-log-item health-log-item--${issue.level}`}
                  >
                    {issue.time && <span className="health-log-time">{issue.time}</span>}
                    <span className="health-log-message">{issue.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
};
