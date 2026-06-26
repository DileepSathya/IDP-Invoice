import React, { useEffect, useState } from "react";
import {
  fetchLicenseProfile,
  fetchPipelineStatus,
  type LicenseProfile,
  type PipelineStatus,
} from "../api";
import { PlanBanner } from "../components/PlanBanner";

type StatusCardProps = {
  label: string;
  value: number;
  hint: string;
  tone?: "default" | "queue" | "active" | "success" | "warning" | "danger";
};

const StatusCard: React.FC<StatusCardProps> = ({
  label,
  value,
  hint,
  tone = "default",
}) => (
  <div className={`pipeline-stat-card pipeline-stat-card--${tone}`}>
    <span className="pipeline-stat-label">{label}</span>
    <span className="pipeline-stat-value">{value}</span>
    <span className="pipeline-stat-hint">{hint}</span>
  </div>
);

export const AnalyticsDashboard: React.FC = () => {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [licenseProfile, setLicenseProfile] = useState<LicenseProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [pipeline, profile] = await Promise.all([
          fetchPipelineStatus(),
          fetchLicenseProfile(),
        ]);
        if (cancelled) return;
        setStatus(pipeline);
        setLicenseProfile(profile);
        setError(null);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load dashboard");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const intervalId = window.setInterval(() => {
      void fetchPipelineStatus()
        .then((data) => {
          if (!cancelled) {
            setStatus(data);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : "Failed to refresh status");
          }
        });
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  const updatedAt = status?.generated_at
    ? new Date(status.generated_at).toLocaleString()
    : null;

  return (
    <div className="panel analytics-dashboard">
      {licenseProfile && <PlanBanner profile={licenseProfile} />}

      <div className="panel-header">
        <div>
          <h2>Dashboard</h2>
          <p>
            Live pipeline status for folder drops and UI uploads. Refreshes every 4 seconds.
          </p>
        </div>
        {updatedAt && <span className="analytics-updated">Updated {updatedAt}</span>}
      </div>

      {loading && !status && <p className="status-text">Loading pipeline status…</p>}
      {error && <p className="error-text">{error}</p>}

      {status && (
        <>
          <section className="pipeline-stats-grid">
            <StatusCard
              label="Awaiting processing"
              value={status.awaiting_processing}
              hint={`${status.queue_total} file(s) in to_be_processed`}
              tone="queue"
            />
            <StatusCard
              label="In process"
              value={status.in_process}
              hint={
                status.watcher_active
                  ? "Folder watcher is running OCR/Gemini"
                  : status.api_staging > 0
                    ? `${status.api_staging} UI upload(s) in staging`
                    : status.async_jobs > 0
                      ? `${status.async_jobs} async API job(s)`
                      : "Nothing actively processing"
              }
              tone="active"
            />
            <StatusCard
              label="Processed"
              value={status.processed}
              hint="Files in Completed folder"
              tone="success"
            />
            <StatusCard
              label="HITL pending"
              value={status.hitl_pending}
              hint="Files awaiting human review"
              tone="warning"
            />
            <StatusCard
              label="Errors"
              value={status.error}
              hint="Files moved to ERROR folder"
              tone="danger"
            />
          </section>

          <section className="analytics-section">
            <h3>Stored records (MongoDB)</h3>
            <div className="analytics-metrics-grid">
              <div className="analytics-metric">
                <span className="analytics-metric-label">Total stored</span>
                <span className="analytics-metric-value">{status.stored_total}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">Healthy</span>
                <span className="analytics-metric-value">{status.stored_healthy}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">Stored errors</span>
                <span className="analytics-metric-value">{status.stored_errors}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">HITL flagged</span>
                <span className="analytics-metric-value">{status.hitl_flagged_total}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">HITL review pending</span>
                <span className="analytics-metric-value">{status.hitl_review_pending}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">HITL reviewed</span>
                <span className="analytics-metric-value">{status.hitl_reviewed}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">System processed</span>
                <span className="analytics-metric-value">{status.system_processed}</span>
              </div>
              <div className="analytics-metric">
                <span className="analytics-metric-label">Human approved</span>
                <span className="analytics-metric-value">{status.human_approved_files}</span>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
};
