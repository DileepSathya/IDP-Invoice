import React, { useEffect, useState } from "react";
import {
  HitlSmtpSettings,
  fetchHitlSmtpSettings,
  saveHitlSmtpSettings,
} from "../api";

export const SenderEmailSettings: React.FC = () => {
  const [settings, setSettings] = useState<HitlSmtpSettings | null>(null);
  const [useTls, setUseTls] = useState(true);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("587");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [fromAddr, setFromAddr] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const data = await fetchHitlSmtpSettings();
        setSettings(data);
        setUseTls(data.use_tls);
        setHost(data.host);
        setPort(String(data.port));
        setUser(data.user);
        setFromAddr(data.from_addr);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load SMTP sender settings");
      }
    })();
  }, []);

  const handleSave = async () => {
    const numericPort = Number(port);
    if (!host.trim() || !fromAddr.trim()) {
      setError("SMTP host and sender email address are required.");
      return;
    }
    if (!Number.isInteger(numericPort) || numericPort < 1 || numericPort > 65535) {
      setError("SMTP port must be a whole number between 1 and 65535.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const saved = await saveHitlSmtpSettings({
        use_tls: useTls,
        host: host.trim(),
        port: numericPort,
        user: user.trim(),
        password,
        from_addr: fromAddr.trim(),
      });
      setSettings(saved);
      setPassword("");
      setMessage("Sender email settings saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save SMTP sender settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-page-content">
      <div className="settings-content-header">
        <div>
          <h2>Configure Sender Email</h2>
          <p>Store the SMTP server details used to send notification emails.</p>
        </div>
      </div>
      <section className="panel-section erp-settings-section">
        <div className="panel-section-main sender-email-form">
          {error && <div className="alert alert-error">{error}</div>}
          {message && !error && <div className="alert">{message}</div>}
          <label className="smtp-tls-toggle">
            <span><strong>Use TLS</strong></span>
            <input
              type="checkbox"
              role="switch"
              checked={useTls}
              onChange={(e) => setUseTls(e.target.checked)}
              aria-label="Use TLS"
            />
            <span className="smtp-tls-toggle-track" aria-hidden="true" />
          </label>
          <label className="notification-settings-field">
            <span>SMTP Host</span>
            <input value={host} onChange={(e) => setHost(e.target.value)} autoComplete="off" />
          </label>
          <label className="notification-settings-field">
            <span>SMTP Port</span>
            <input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
          </label>
          <label className="notification-settings-field">
            <span>SMTP User</span>
            <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" />
          </label>
          <label className="notification-settings-field">
            <span>SMTP Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder={settings?.configured ? "Leave blank to keep saved password" : ""} />
          </label>
          <label className="notification-settings-field">
            <span>SMTP From</span>
            <input type="email" value={fromAddr} onChange={(e) => setFromAddr(e.target.value)} placeholder="sender email address" autoComplete="email" />
          </label>
          <div className="erp-settings-actions">
            <button type="button" onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Saving…" : "Save Sender Email"}
            </button>
          </div>
        </div>
      </section>
    </section>
  );
};
