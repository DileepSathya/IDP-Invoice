import React, { useCallback, useEffect, useMemo, useState } from "react";
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

const TIME_PATTERN = /^([01]\d|2[0-3]):([0-5]\d)$/;
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"));

function parseTimeParts(value: string): { hour: string; minute: string } {
  const match = TIME_PATTERN.exec(value.trim());
  if (match) {
    return { hour: match[1], minute: match[2] };
  }
  return { hour: "08", minute: "00" };
}

function formatTimeParts(hour: string, minute: string): string {
  return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
}

type ScheduledTimePickerProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
};

const ScheduledTimePicker: React.FC<ScheduledTimePickerProps> = ({
  value,
  onChange,
  disabled = false,
  label,
}) => {
  const { hour, minute } = parseTimeParts(value);

  return (
    <div className="tally-scheduled-time-picker" role="group" aria-label={label}>
      <select
        className="tally-scheduled-time-part"
        value={hour}
        onChange={(e) => onChange(formatTimeParts(e.target.value, minute))}
        disabled={disabled}
        aria-label={`${label ?? "Scheduled time"} hour`}
      >
        {HOUR_OPTIONS.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
      <span className="tally-scheduled-time-separator" aria-hidden="true">
        :
      </span>
      <select
        className="tally-scheduled-time-part"
        value={minute}
        onChange={(e) => onChange(formatTimeParts(hour, e.target.value))}
        disabled={disabled}
        aria-label={`${label ?? "Scheduled time"} minute`}
      >
        {MINUTE_OPTIONS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
};

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never";
  return d.toLocaleString();
}

function detectBrowserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function normalizeScheduledTimes(times: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of times) {
    const trimmed = raw.trim();
    if (!TIME_PATTERN.test(trimmed)) continue;
    const [h, m] = trimmed.split(":");
    const canonical = `${h}:${m}`;
    if (seen.has(canonical)) continue;
    seen.add(canonical);
    normalized.push(canonical);
  }
  normalized.sort((a, b) => {
    const [ah, am] = a.split(":").map(Number);
    const [bh, bm] = b.split(":").map(Number);
    return ah * 60 + am - (bh * 60 + bm);
  });
  return normalized;
}

function formatNextRefresh(settings: TallyMasterSchedulerSettings | null): string {
  if (!settings || settings.mode === "manual") return "Manual only";
  if (settings.mode === "time_based") {
    const times = settings.scheduled_times.join(", ");
    const tz = settings.timezone;
    const next = settings.next_refresh_at;
    if (!times) return "No times configured";
    if (next) return `${formatTimestamp(next)} (${tz}) — daily at ${times}`;
    return `Daily at ${times} (${tz})`;
  }
  return formatTimestamp(settings.next_refresh_at);
}

export const TallyMasterSettings: React.FC = () => {
  const [status, setStatus] = useState<TallyMasterSyncStatus | null>(null);
  const [schedulerSettings, setSchedulerSettings] = useState<TallyMasterSchedulerSettings | null>(
    null,
  );
  const [modeInput, setModeInput] = useState<TallyMasterSchedulerMode>("manual");
  const [frequencyInput, setFrequencyInput] = useState("60");
  const [scheduledTimesInput, setScheduledTimesInput] = useState<string[]>([]);
  const [timezoneInput, setTimezoneInput] = useState(detectBrowserTimezone());
  const [scheduledRematchInput, setScheduledRematchInput] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [rematchAfterRefresh, setRematchAfterRefresh] = useState(true);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [schedulerError, setSchedulerError] = useState<string | null>(null);
  const [schedulerMessage, setSchedulerMessage] = useState<string | null>(null);

  const timezoneOptions = useMemo(() => {
    try {
      return Intl.supportedValuesOf("timeZone").slice().sort();
    } catch {
      return ["UTC", "Asia/Kolkata", "America/New_York", "Europe/London"];
    }
  }, []);

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
          setScheduledTimesInput(data.scheduled_times ?? []);
          setTimezoneInput(data.timezone || detectBrowserTimezone());
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

  const handleAddScheduledTime = () => {
    setScheduledTimesInput((prev) => [...prev, "08:00"]);
  };

  const handleScheduledTimeChange = (index: number, value: string) => {
    setScheduledTimesInput((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  };

  const handleRemoveScheduledTime = (index: number) => {
    setScheduledTimesInput((prev) => prev.filter((_, i) => i !== index));
  };

  const validateSchedulerInputs = (): string | null => {
    if (modeInput === "scheduled") {
      const frequency = parseInt(frequencyInput, 10);
      if (!Number.isFinite(frequency) || frequency < 1) {
        return "Frequency must be a whole number of minutes (1 or more).";
      }
    }
    if (modeInput === "time_based") {
      if (scheduledTimesInput.length === 0) {
        return "Add at least one scheduled time.";
      }
      const normalized = normalizeScheduledTimes(scheduledTimesInput);
      if (normalized.length !== scheduledTimesInput.length) {
        return "Remove duplicate scheduled times and use valid 24-hour HH:mm values.";
      }
      for (const time of scheduledTimesInput) {
        if (!TIME_PATTERN.test(time.trim())) {
          return `Invalid time "${time}". Use 24-hour HH:mm format (e.g. 08:00, 13:30).`;
        }
      }
      const unique = new Set(scheduledTimesInput.map((t) => {
        const [h, m] = t.trim().split(":");
        return `${h}:${m}`;
      }));
      if (unique.size !== scheduledTimesInput.length) {
        return "Duplicate scheduled times are not allowed.";
      }
      if (!timezoneInput.trim()) {
        return "Select a timezone for time-based scheduling.";
      }
    }
    return null;
  };

  const handleSaveSchedulerSettings = async () => {
    const validationError = validateSchedulerInputs();
    if (validationError) {
      setSchedulerError(validationError);
      return;
    }

    const frequency = parseInt(frequencyInput, 10);
    const normalizedTimes =
      modeInput === "time_based" ? normalizeScheduledTimes(scheduledTimesInput) : undefined;

    try {
      setSavingScheduler(true);
      setSchedulerError(null);
      setSchedulerMessage(null);
      const data = await saveTallyMasterSchedulerSettings({
        mode: modeInput,
        frequency_minutes: Number.isFinite(frequency) && frequency >= 1 ? frequency : 60,
        rematch_after_scheduled_refresh: scheduledRematchInput,
        scheduled_times: normalizedTimes,
        timezone: modeInput === "time_based" ? timezoneInput : undefined,
      });
      setSchedulerSettings(data);
      setModeInput(data.mode);
      setFrequencyInput(String(data.frequency_minutes));
      setScheduledTimesInput(data.scheduled_times ?? []);
      setTimezoneInput(data.timezone || detectBrowserTimezone());
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
  const isScheduledMode = modeInput === "scheduled" || modeInput === "time_based";

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
                <span className="plan-detail-value">{formatNextRefresh(schedulerSettings)}</span>
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
            Schedule automatic pulls from Tally by interval or specific times each day, or use
            manual refresh only. The Refresh button below always works regardless of scheduler
            mode.
          </p>

          {schedulerError && <div className="alert alert-error">{schedulerError}</div>}
          {schedulerMessage && !schedulerError && <div className="alert">{schedulerMessage}</div>}

          <div className="erp-settings-grid">
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="tally-master-scheduler-mode"
                value="scheduled"
                checked={modeInput === "scheduled"}
                onChange={() => setModeInput("scheduled")}
              />
              <span>
                <strong>Interval based run</strong> — automatically refresh from Tally every
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

            <label className="erp-settings-radio">
              <input
                type="radio"
                name="tally-master-scheduler-mode"
                value="time_based"
                checked={modeInput === "time_based"}
                onChange={() => setModeInput("time_based")}
              />
              <span>
                <strong>Time based run</strong> — refresh once each day at the scheduled times
                below (24-hour clock).
              </span>
            </label>

            <label className="erp-settings-radio">
              <input
                type="radio"
                name="tally-master-scheduler-mode"
                value="manual"
                checked={modeInput === "manual"}
                onChange={() => setModeInput("manual")}
              />
              <span>
                <strong>Manual run</strong> — refresh master data only when you click Refresh
                from Tally.
              </span>
            </label>
          </div>

          {modeInput === "time_based" && (
            <div className="tally-scheduled-times-section">
              <label className="ledger-settings-field">
                <span className="ledger-settings-label">Timezone</span>
                <select
                  className="tally-timezone-select"
                  value={timezoneInput}
                  onChange={(e) => setTimezoneInput(e.target.value)}
                  disabled={savingScheduler}
                  aria-label="Scheduler timezone"
                >
                  {timezoneOptions.map((tz) => (
                    <option key={tz} value={tz}>
                      {tz}
                    </option>
                  ))}
                </select>
              </label>

              <div className="tally-scheduled-times-header">
                <span className="ledger-settings-label">Scheduled times</span>
              </div>

              {scheduledTimesInput.length > 0 && (
                <ul className="tally-scheduled-times-list">
                  {scheduledTimesInput.map((time, index) => (
                    <li key={`${index}-${time}`} className="tally-scheduled-time-row">
                      <ScheduledTimePicker
                        value={time}
                        onChange={(next) => handleScheduledTimeChange(index, next)}
                        disabled={savingScheduler}
                        label={`Scheduled time ${index + 1}`}
                      />
                      <button
                        type="button"
                        className="button-link tally-scheduled-time-delete"
                        onClick={() => handleRemoveScheduledTime(index)}
                        disabled={savingScheduler}
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <button
                type="button"
                className="tally-add-time-button"
                onClick={handleAddScheduledTime}
                disabled={savingScheduler}
              >
                + Add time
              </button>
            </div>
          )}

          <label className="ledger-settings-field" style={{ marginTop: "0.75rem" }}>
            <span className="ledger-settings-label">
              <input
                type="checkbox"
                checked={scheduledRematchInput}
                onChange={(e) => setScheduledRematchInput(e.target.checked)}
                disabled={!isScheduledMode || savingScheduler}
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
