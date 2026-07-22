import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ErpSyncSettings,
  ErpSyncMode,
  fetchErpSyncSettings,
  saveErpSyncSettings,
  forceErpSync,
} from "../api";

export const ErpSettings: React.FC = () => {
  const [erpSettings, setErpSettings] = useState<ErpSyncSettings | null>(null);
  const [modeInput, setModeInput] = useState<ErpSyncMode>("immediate");
  const [frequencyInput, setFrequencyInput] = useState("15");
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [forcingSyncNow, setForcingSyncNow] = useState(false);

  // Only seed the mode/frequency form fields from the server on the very first load - once
  // erpSettings is non-null, later polls (every 5s) keep last_synced_at/syncing fresh without
  // clobbering whatever the user is currently typing/selecting before they hit Save.
  const loadErpSettings = useCallback(async (options?: { silent?: boolean }) => {
    try {
      if (!options?.silent) setSettingsError(null);
      const data = await fetchErpSyncSettings();
      setErpSettings((prev) => {
        if (prev === null) {
          setModeInput(data.mode);
          setFrequencyInput(String(data.frequency_minutes));
        }
        return data;
      });
    } catch (e) {
      if (!options?.silent) {
        setSettingsError(e instanceof Error ? e.message : "Failed to load ERP sync settings");
      }
    }
  }, []);

  useEffect(() => {
    void loadErpSettings();
    const intervalId = window.setInterval(() => void loadErpSettings({ silent: true }), 5000);
    return () => window.clearInterval(intervalId);
  }, [loadErpSettings]);

  const handleSaveErpSettings = async () => {
    const frequency = parseInt(frequencyInput, 10);
    if (modeInput === "scheduled" && (!Number.isFinite(frequency) || frequency < 1)) {
      setSettingsError("Frequency must be a whole number of minutes (1 or more).");
      return;
    }
    try {
      setSavingSettings(true);
      setSettingsError(null);
      setSettingsMessage(null);
      const data = await saveErpSyncSettings(
        modeInput,
        Number.isFinite(frequency) && frequency >= 1 ? frequency : 15,
      );
      setErpSettings(data);
      setModeInput(data.mode);
      setFrequencyInput(String(data.frequency_minutes));
      setSettingsMessage("ERP sync settings saved.");
    } catch (e) {
      setSettingsError(e instanceof Error ? e.message : "Failed to save ERP sync settings");
    } finally {
      setSavingSettings(false);
    }
  };

  const handleForceSync = async () => {
    try {
      setForcingSyncNow(true);
      setSettingsError(null);
      setSettingsMessage(null);
      const data = await forceErpSync();
      setErpSettings(data);
      setSettingsMessage("Sync started - this page updates automatically as it runs.");
    } catch (e) {
      setSettingsError(e instanceof Error ? e.message : "Failed to start ERP sync");
    } finally {
      setForcingSyncNow(false);
    }
  };

  const erpSyncSuccessful =
    !!erpSettings?.configured &&
    !erpSettings?.syncing &&
    !!erpSettings?.last_sync_result &&
    erpSettings.last_sync_result.errored === 0;

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <h2>ERP Sync Settings</h2>
          <p>
            Configure how invoices are matched against the Postgres PO_DB reference tables.
          </p>
        </div>
        <Link to="/erp" className="button-link">
          ← Back to ERP
        </Link>
      </div>

      <section className="panel-section erp-settings-section">
        <div className="panel-section-main">
          {erpSettings && !erpSettings.configured && (
            <div className="alert">
              PO_DB is not configured yet (POSTGRES_HOST is not set) — matching will show every
              invoice as unmatched, and invoice downloads are disabled until a sync succeeds.
            </div>
          )}
          {erpSettings?.configured && !erpSyncSuccessful && (
            <div className="alert">
              {erpSettings.syncing
                ? "ERP sync in progress — invoice downloads unlock once it finishes cleanly."
                : erpSettings.last_sync_result && erpSettings.last_sync_result.errored > 0
                ? `The last sync finished with ${erpSettings.last_sync_result.errored} error(s) — invoice downloads stay disabled until a clean sync completes.`
                : "No successful sync yet — invoice downloads are disabled until one completes."}
            </div>
          )}
          {settingsError && <div className="alert alert-error">{settingsError}</div>}
          {settingsMessage && !settingsError && <div className="alert">{settingsMessage}</div>}

          <div className="erp-settings-grid">
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="erp-sync-mode"
                value="immediate"
                checked={modeInput === "immediate"}
                onChange={() => setModeInput("immediate")}
              />
              <span>
                <strong>Immediate</strong> — match each invoice against PO_DB the moment it's
                processed, and again immediately whenever you edit it.
              </span>
            </label>
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="erp-sync-mode"
                value="scheduled"
                checked={modeInput === "scheduled"}
                onChange={() => setModeInput("scheduled")}
              />
              <span>
                <strong>Scheduled</strong> — edits to invoices wait for the next sync instead of
                matching immediately; re-sync every
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
                  aria-label="Sync frequency in minutes"
                />
                minutes so both your edits and any vendor/item/PO changes made directly in
                Postgres reach invoices together.
              </span>
            </label>
          </div>

          <div className="erp-settings-actions">
            <button type="button" onClick={() => void handleSaveErpSettings()} disabled={savingSettings}>
              {savingSettings ? "Saving…" : "Save Settings"}
            </button>
            <button
              type="button"
              onClick={() => void handleForceSync()}
              disabled={forcingSyncNow || !!erpSettings?.syncing || !erpSettings?.configured}
            >
              {erpSettings?.syncing ? "Syncing…" : forcingSyncNow ? "Starting…" : "Force Sync"}
            </button>
          </div>
          <p className="search-hint">
            <Link to="/erp">See last/next sync time on the ERP page →</Link>
          </p>
        </div>
      </section>
    </div>
  );
};
