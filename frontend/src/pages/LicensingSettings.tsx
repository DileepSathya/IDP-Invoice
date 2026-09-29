import React, { useEffect, useState } from "react";
import { fetchLicenseProfile, saveLicenseKey, type LicenseProfile } from "../api";
import { useLicenseProfile } from "../context/LicenseProfileContext";

export const LicensingSettings: React.FC = () => {
  const { refreshLicense } = useLicenseProfile();
  const [profile, setProfile] = useState<LicenseProfile | null>(null);
  const [licenseKey, setLicenseKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchLicenseProfile()
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Failed to load license status");
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
    const trimmed = licenseKey.trim();
    if (!trimmed) {
      setError("Paste your license key before saving.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const data = await saveLicenseKey(trimmed);
      setProfile(data);
      setLicenseKey("");
      setMessage("License saved and validated.");
      await refreshLicense();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save license key");
    } finally {
      setSaving(false);
    }
  };

  const fingerprint = profile?.machineFingerprint ?? "";

  return (
    <section className="settings-page-content" aria-labelledby="licensing-settings-title">
      <div className="settings-content-header">
        <h3 id="licensing-settings-title">Licensing</h3>
        <p>
          Paste the license key issued for this machine. The key is stored in MongoDB and validated
          on each application start.
        </p>
      </div>
      {loading && <p className="settings-loading">Loading license status…</p>}
      {error && <div className="alert alert-error">{error}</div>}
      {message && !error && <div className="alert">{message}</div>}
      {!loading && profile && (
        <div className="settings-form-card">
          <div className="account-profile-card" style={{ marginBottom: "1rem" }}>
            <div className="account-profile-row">
              <span className="account-profile-label">Status</span>
              <span className="account-profile-value">
                {profile.licensed ? profile.statusMessage : profile.validationError || profile.statusMessage}
              </span>
            </div>
            {profile.licensed && profile.customerId && (
              <div className="account-profile-row">
                <span className="account-profile-label">Account</span>
                <span className="account-profile-value">{profile.customerId}</span>
              </div>
            )}
            {profile.licensed && (
              <div className="account-profile-row">
                <span className="account-profile-label">Plan</span>
                <span className="account-profile-value">{profile.planLabel}</span>
              </div>
            )}
            {fingerprint && (
              <div className="account-profile-row">
                <span className="account-profile-label">Machine fingerprint</span>
                <span className="account-profile-value" style={{ wordBreak: "break-all", fontSize: "0.85rem" }}>
                  {fingerprint}
                </span>
              </div>
            )}
          </div>
          <label className="settings-form-field" htmlFor="license-key">
            <span>License key</span>
            <textarea
              id="license-key"
              rows={6}
              value={licenseKey}
              onChange={(event) => setLicenseKey(event.target.value)}
              placeholder="Paste the full license key (base64 string from your vendor)"
              spellCheck={false}
              autoComplete="off"
            />
          </label>
          <p className="search-hint">
            Run the fingerprint tool on this machine and send the hash to your vendor to receive a
            key bound to this installation.
          </p>
          <div className="settings-form-actions">
            <button type="button" onClick={() => void handleSave()} disabled={saving || !licenseKey.trim()}>
              {saving ? "Saving…" : "Save license key"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
};
