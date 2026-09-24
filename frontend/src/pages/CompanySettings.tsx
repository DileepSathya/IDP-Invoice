import React, { useEffect, useState } from "react";
import { fetchTallyCompanySettings, saveTallyCompanySettings } from "../api";

const DEFAULT_TALLY_PORT = 9000;

export const CompanySettings: React.FC = () => {
  const [companyName, setCompanyName] = useState("");
  const [tallyHost, setTallyHost] = useState("");
  const [tallyPort, setTallyPort] = useState(String(DEFAULT_TALLY_PORT));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchTallyCompanySettings()
      .then((data) => {
        if (cancelled) return;
        setCompanyName(data.company_name);
        setTallyHost(data.tally_host);
        setTallyPort(String(data.tally_port ?? DEFAULT_TALLY_PORT));
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Failed to load company details");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSave = async () => {
    if (!companyName.trim()) {
      setError("Company name is required.");
      return;
    }
    if (!tallyHost.trim()) {
      setError("IP address is required.");
      return;
    }
    const port = Number.parseInt(tallyPort, 10);
    if (!Number.isFinite(port) || port < 1 || port > 65535) {
      setError("Port must be a number between 1 and 65535.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const data = await saveTallyCompanySettings({
        company_name: companyName,
        tally_host: tallyHost,
        tally_port: port,
      });
      setCompanyName(data.company_name);
      setTallyHost(data.tally_host);
      setTallyPort(String(data.tally_port));
      setMessage("Company details saved and applied immediately.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save company details");
    } finally {
      setSaving(false);
    }
  };

  const canSave =
    companyName.trim().length > 0 &&
    tallyHost.trim().length > 0 &&
    tallyPort.trim().length > 0;

  return (
    <section className="settings-page-content" aria-labelledby="company-settings-title">
      <div className="settings-content-header">
        <h3 id="company-settings-title">Company Details</h3>
        <p>Enter the company name exactly as it appears in Tally Prime.</p>
      </div>
      {loading && <p className="settings-loading">Loading company details…</p>}
      {error && <div className="alert alert-error">{error}</div>}
      {message && !error && <div className="alert">{message}</div>}
      {!loading && (
        <div className="settings-form-card">
          <label className="settings-form-field" htmlFor="tally-company-name">
            <span>Company name</span>
            <input
              id="tally-company-name"
              type="text"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              placeholder="Example: Amoga Industries Pvt Ltd"
              autoComplete="organization"
            />
          </label>

          <fieldset className="settings-fieldset">
            <legend>Connect to Tally</legend>
            <label className="settings-form-field" htmlFor="tally-host">
              <span>IP address</span>
              <input
                id="tally-host"
                type="text"
                value={tallyHost}
                onChange={(event) => setTallyHost(event.target.value)}
                placeholder="Example: 192.168.1.10 or localhost"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label className="settings-form-field" htmlFor="tally-port">
              <span>Port</span>
              <input
                id="tally-port"
                type="number"
                min={1}
                max={65535}
                value={tallyPort}
                onChange={(event) => setTallyPort(event.target.value)}
                placeholder={String(DEFAULT_TALLY_PORT)}
              />
            </label>
          </fieldset>

          <p className="search-hint">
            Changes apply to Tally master-data refreshes and invoice pushes immediately. TallyPrime
            must expose its HTTP server on this address (often port 9000).
          </p>
          <div className="settings-form-actions">
            <button type="button" onClick={() => void handleSave()} disabled={saving || !canSave}>
              {saving ? "Saving…" : "Save Company Details"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
};
