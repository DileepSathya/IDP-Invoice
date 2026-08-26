import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchTallyMasterSchedulerSettings,
  fetchTallyMasterStatus,
  refreshTallyMasterData,
  saveTallyMasterSchedulerSettings,
  type TallyMasterSchedulerMode,
  type TallyMasterSchedulerSettings,
  type TallyMasterSyncStatus,
} from "../api";

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never";
  return d.toLocaleString();
}

export const TallyMasterSettings: React.FC = () => {
  const [status, setStatus] = useState<TallyMasterSyncStatus | null>(null);
  const [schedulerSettings, setSchedulerSettings] = useState<TallyMasterSchedulerSettings | null>(
    null,
  );
  const [modeInput, setModeInput] = useState<TallyMasterSchedulerMode>("manual");
  const [frequencyInput, setFrequencyInput] = useState("60");
  const [scheduledRematchInput, setScheduledRematchInput] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [rematchAfterRefresh, setRematchAfterRefresh] = useState(true);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [schedulerError, setSchedulerError] = useState<string | null>(null);
  const [schedulerMessage, setSchedulerMessage] = useState<string | null>(null);

  const loadStatus = useCallback(async (options?: { silent?: boolean }) => {
    try {
      if (!options?.silent) {
        setLoading(true);
        setError(null);
      }
      const data = await fetchTallyMasterStatus();
      setStatus(data);
    } catch (e) {
      if (!options?.silent) {
        setError(e instanceof Error ? e.message : "Failed to load Tally master data status");
      }
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, []);

  const loadSchedulerSettings = useCallback(async (options?: { silent?: boolean }) => {
    try {
      if (!options?.silent) {
        setSchedulerError(null);
      }
      const data = await fetchTallyMasterSchedulerSettings();
      setSchedulerSettings((prev) => {
        if (prev === null) {
          setModeInput(data.mode);
          setFrequencyInput(String(data.frequency_minutes));
          setScheduledRematchInput(data.rematch_after_scheduled_refresh);
        }
        return data;
      });
    } catch (e) {
      if (!options?.silent) {
        setSchedulerError(
          e instanceof Error ? e.message : "Failed to load Tally scheduler settings",
        );
      }
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadSchedulerSettings();
    const intervalId = window.setInterval(() => {
      void loadStatus({ silent: true });
      void loadSchedulerSettings({ silent: true });
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [loadStatus, loadSchedulerSettings]);

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      setError(null);
      setMessage(null);
      const data = await refreshTallyMasterData(rematchAfterRefresh);
      if (data.status) {
        setStatus(data.status);
      }
      setMessage(data.message);
      if (!data.started) {
        setError(data.message);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh Tally master data");
    } finally {
      setRefreshing(false);
    }
  };

  const handleSaveSchedulerSettings = async () => {
    const frequency = parseInt(frequencyInput, 10);
    if (modeInput === "scheduled" && (!Number.isFinite(frequency) || frequency < 1)) {
      setSchedulerError("Frequency must be a whole number of minutes (1 or more).");
      return;
    }
    try {
      setSavingScheduler(true);
      setSchedulerError(null);
      setSchedulerMessage(null);
      const data = await saveTallyMasterSchedulerSettings(
        modeInput,
        Number.isFinite(frequency) && frequency >= 1 ? frequency : 60,
        scheduledRematchInput,
      );
      setSchedulerSettings(data);
      setModeInput(data.mode);
      setFrequencyInput(String(data.frequency_minutes));
      setScheduledRematchInput(data.rematch_after_scheduled_refresh);
      setSchedulerMessage("Scheduler settings saved.");
    } catch (e) {
      setSchedulerError(
        e instanceof Error ? e.message : "Failed to save Tally scheduler settings",
      );
    } finally {
      setSavingScheduler(false);
    }
  };

  const counts = status?.counts ?? {};
  const lastResult = status?.last_result;

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <h2>Tally Master Data</h2>
          <p>
            Pull vendors (Sundry Creditors), stock items, and purchase orders from Tally Prime
            into MongoDB. Invoice matching reads this cached data — Tally is only contacted when
            you refresh manually or on the scheduler interval.
          </p>
        </div>
        <Link to="/settings" className="button-link">
          ← Back to Settings
        </Link>
      </div>

      <section className="panel-section erp-settings-section">
        <div className="panel-section-main">
          {loading && !status && <p className="settings-loading">Loading status…</p>}

          {status && !status.tally_configured && (
            <div className="alert">
              Tally integration is disabled. Enable it in <code>.env</code> with{" "}
              <code>TALLY_ENABLED=true</code>, set <code>TALLY_BRIDGE_URL</code>, and configure{" "}
              <code>tally-bridge\.env</code> with <code>TALLY_COMPANY</code>.
            </div>
          )}

          {status?.company && (
            <p className="ledger-settings-company">
              Tally company: <strong>{status.company}</strong>
            </p>
          )}

          {status && (
            <div className="plan-details-grid">
              <div className="plan-detail-row">
                <span className="plan-detail-label">Vendors</span>
                <span className="plan-detail-value">{counts.vendors ?? 0}</span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">Stock items</span>
                <span className="plan-detail-value">{counts.items ?? 0}</span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">Purchase orders</span>
                <span className="plan-detail-value">{counts.po_headers ?? 0}</span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">PO line items</span>
                <span className="plan-detail-value">{counts.po_lines ?? 0}</span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">Last refreshed</span>
                <span className="plan-detail-value">{formatTimestamp(status.last_synced_at)}</span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">Next scheduled refresh</span>
                <span className="plan-detail-value">
                  {schedulerSettings?.mode === "scheduled"
                    ? formatTimestamp(schedulerSettings.next_refresh_at)
                    : "Manual only"}
                </span>
              </div>
              <div className="plan-detail-row">
                <span className="plan-detail-label">ERP matching</span>
                <span className="plan-detail-value">
                  {status.configured ? "Ready" : "Not ready — refresh master data first"}
                </span>
              </div>
            </div>
          )}

          {status?.syncing && (
            <div className="alert">Refresh in progress — counts update when complete.</div>
          )}

          {status?.last_error && !status.syncing && (
            <div className="alert alert-error">{status.last_error}</div>
          )}

          {lastResult?.errors && lastResult.errors.length > 0 && !status?.syncing && (
            <div className="alert alert-error">
              Last refresh warnings: {lastResult.errors.join("; ")}
            </div>
          )}

          {error && <div className="alert alert-error">{error}</div>}
          {message && !error && <div className="alert">{message}</div>}

          <h3 style={{ marginTop: "1.5rem" }}>Refresh scheduler</h3>
          <p className="search-hint">
            Schedule automatic pulls from Tally, or use manual refresh only. The Refresh button
            below always works regardless of scheduler mode.
          </p>

          {schedulerError && <div className="alert alert-error">{schedulerError}</div>}
          {schedulerMessage && !schedulerError && <div className="alert">{schedulerMessage}</div>}

          <div className="erp-settings-grid">
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="tally-master-scheduler-mode"
                value="manual"
                checked={modeInput === "manual"}
                onChange={() => setModeInput("manual")}
              />
              <span>
                <strong>Manual only</strong> — refresh master data only when you click Refresh
                from Tally.
              </span>
            </label>
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="tally-master-scheduler-mode"
                value="scheduled"
                checked={modeInput === "scheduled"}
                onChange={() => setModeInput("scheduled")}
              />
              <span>
                <strong>Scheduled</strong> — automatically refresh from Tally every
                <input
                  type="number"
                  min={1}
                  max={1440}
                  step={1}
                  className="erp-settings-frequency-input"
                  value={frequencyInput}
                  onChange={(e) => setFrequencyInput(e.target.value)}
                  onFocus={() => setModeInput("scheduled")}
                  disabled={modeInput !== "scheduled"}
                  aria-label="Refresh frequency in minutes"
                />
                minutes.
              </span>
            </label>
          </div>

          <label className="ledger-settings-field" style={{ marginTop: "0.75rem" }}>
            <span className="ledger-settings-label">
              <input
                type="checkbox"
                checked={scheduledRematchInput}
                onChange={(e) => setScheduledRematchInput(e.target.checked)}
                disabled={modeInput !== "scheduled" || savingScheduler}
              />{" "}
              Re-match all invoices after each scheduled refresh
            </span>
          </label>

          <div className="erp-settings-actions">
            <button
              type="button"
              onClick={() => void handleSaveSchedulerSettings()}
              disabled={savingScheduler || !status?.tally_configured}
            >
              {savingScheduler ? "Saving…" : "Save Scheduler Settings"}
            </button>
          </div>

          <h3 style={{ marginTop: "1.5rem" }}>Manual refresh</h3>

          <label className="ledger-settings-field" style={{ marginTop: "0.5rem" }}>
            <span className="ledger-settings-label">
              <input
                type="checkbox"
                checked={rematchAfterRefresh}
                onChange={(e) => setRematchAfterRefresh(e.target.checked)}
                disabled={refreshing || status?.syncing}
              />{" "}
              Re-match all invoices after refresh
            </span>
          </label>

          <div className="erp-settings-actions">
            <button
              type="button"
              onClick={() => void handleRefresh()}
              disabled={refreshing || status?.syncing || !status?.tally_configured}
            >
              {refreshing || status?.syncing ? "Refreshing…" : "Refresh from Tally"}
            </button>
            <Link to="/settings/ledger" className="button-link">
              Ledger Settings
            </Link>
          </div>

          <p className="search-hint">
            After the first refresh, use ERP → Force Re-match to re-match invoices without hitting
            Tally again. Configure the purchase ledger under Ledger Settings before pushing
            vouchers.
          </p>
        </div>
      </section>
    </div>
  );
};
