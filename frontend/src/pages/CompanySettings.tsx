import React, { useEffect, useState } from "react";
import { fetchTallyCompanySettings, saveTallyCompanySettings } from "../api";

export const CompanySettings: React.FC = () => {
  const [companyName, setCompanyName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchTallyCompanySettings()
      .then((data) => { if (!cancelled) setCompanyName(data.company_name); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Failed to load company details"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const handleSave = async () => {
    if (!companyName.trim()) {
      setError("Company name is required.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const data = await saveTallyCompanySettings(companyName);
      setCompanyName(data.company_name);
      setMessage("Company details saved and applied immediately.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save company details");
    } finally {
      setSaving(false);
    }
  };

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
          <p className="search-hint">Changes apply to Tally master-data refreshes and invoice pushes immediately.</p>
          <div className="settings-form-actions">
            <button type="button" onClick={() => void handleSave()} disabled={saving || !companyName.trim()}>
              {saving ? "Saving…" : "Save Company Details"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
};
