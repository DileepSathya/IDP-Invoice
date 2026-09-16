import React, { useCallback, useEffect, useState } from "react";
import {
  fetchTallyLedgerSettings,
  fetchTallyPurchaseLedgers,
  saveTallyLedgerSettings,
  type TallyLedgerSettings,
} from "../api";

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never";
  return d.toLocaleString();
}

export const LedgerSettings: React.FC = () => {
  const [settings, setSettings] = useState<TallyLedgerSettings | null>(null);
  const [ledgers, setLedgers] = useState<string[]>([]);
  const [selectedLedger, setSelectedLedger] = useState("");
  const [company, setCompany] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [fetchWarning, setFetchWarning] = useState<string | null>(null);

  const loadLedgers = useCallback(async (options?: { silent?: boolean }) => {
    try {
      if (!options?.silent) {
        setRefreshing(true);
        setError(null);
      }
      const data = await fetchTallyPurchaseLedgers();
      setLedgers(data.ledgers ?? []);
      setCompany(data.company ?? null);
      setFetchWarning(data.error ?? null);
      if (!data.tally_configured) {
        setFetchWarning(
          "Tally is not configured. Set TALLY_ENABLED=true and TALLY_BRIDGE_URL in .env.",
        );
      } else if (!data.tally_reachable) {
        setFetchWarning(data.error ?? "Tally bridge or Tally Prime is not reachable.");
      }
    } catch (e) {
      if (!options?.silent) {
        setError(e instanceof Error ? e.message : "Failed to load purchase ledgers from Tally");
      }
    } finally {
      if (!options?.silent) {
        setRefreshing(false);
      }
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchTallyLedgerSettings();
      setSettings(data);
      setSelectedLedger(data.purchase_ledger ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load ledger settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
    void loadLedgers();
  }, [loadSettings, loadLedgers]);

  const savedLedgerMissing =
    !!selectedLedger && ledgers.length > 0 && !ledgers.includes(selectedLedger);

  const handleSave = async () => {
    if (!selectedLedger.trim()) {
      setError("Select a purchase ledger before saving.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const data = await saveTallyLedgerSettings(selectedLedger);
      setSettings(data);
      setSelectedLedger(data.purchase_ledger);
      setMessage("Purchase ledger saved. New Tally pushes will use this ledger.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save ledger settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-page-content">
      <div className="settings-content-header">
        <div>
          <h2>Ledger Settings</h2>
          <p>
            Choose the purchase account ledger from Tally Prime for inventory voucher
            accounting allocations.
          </p>
        </div>
      </div>

      <section className="panel-section erp-settings-section">
        <div className="panel-section-main">
          {loading && <p className="settings-loading">Loading ledger settings…</p>}

          {!loading && settings && !settings.tally_configured && (
            <div className="alert">
              Tally integration is disabled. Enable it in <code>.env</code> with{" "}
              <code>TALLY_ENABLED=true</code> and start the Tally bridge service.
            </div>
          )}

          {fetchWarning && !error && <div className="alert">{fetchWarning}</div>}
          {error && <div className="alert alert-error">{error}</div>}
          {message && !error && <div className="alert">{message}</div>}

          {!loading && (
            <div className="ledger-settings-form">
              {company && (
                <p className="ledger-settings-company">
                  Tally company: <strong>{company}</strong>
                </p>
              )}

              <label className="ledger-settings-field">
                <span className="ledger-settings-label">Purchase ledger</span>
                <select
                  value={selectedLedger}
                  onChange={(e) => setSelectedLedger(e.target.value)}
                  disabled={ledgers.length === 0 || saving}
                  aria-label="Purchase ledger"
                >
                  <option value="">
                    {ledgers.length === 0
                      ? "No purchase ledgers loaded"
                      : "Select purchase ledger"}
                  </option>
                  {savedLedgerMissing && (
                    <option value={selectedLedger}>
                      {selectedLedger} (not found in latest Tally list)
                    </option>
                  )}
                  {ledgers.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>

              {savedLedgerMissing && (
                <p className="search-hint">
                  The saved ledger is not in the current Tally list. Refresh from Tally or pick
                  another ledger.
                </p>
              )}

              <p className="search-hint">
                {ledgers.length > 0
                  ? `${ledgers.length} purchase ledger${ledgers.length === 1 ? "" : "s"} loaded from Tally.`
                  : "Click Refresh from Tally to load ledgers under Purchase Accounts."}
              </p>

              {settings?.updated_at && (
                <p className="erp-settings-last-synced">
                  Last saved: {formatTimestamp(settings.updated_at)}
                </p>
              )}

              <div className="erp-settings-actions">
                <button
                  type="button"
                  onClick={() => void loadLedgers()}
                  disabled={refreshing || saving}
                >
                  {refreshing ? "Refreshing…" : "Refresh from Tally"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving || !selectedLedger.trim() || !settings?.tally_configured}
                >
                  {saving ? "Saving…" : "Save Settings"}
                </button>
              </div>
            </div>
          )}
        </div>
      </section>
    </section>
  );
};
