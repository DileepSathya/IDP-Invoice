import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  HitlNotificationSettings,
  HitlNotificationTriggerMode,
  fetchHitlNotificationSettings,
  saveHitlNotificationSettings,
  sendHitlNotificationTest,
} from "../api";

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never";
  return d.toLocaleString();
}

function formatNextDigest(settings: HitlNotificationSettings | null): string {
  if (!settings?.enabled) return "—";
  if (settings.trigger_mode !== "scheduled_digest") {
    return "Not scheduled (not in digest mode)";
  }
  if (!settings.next_digest_at) return "Pending first digest";
  const next = new Date(settings.next_digest_at);
  if (Number.isNaN(next.getTime())) return "—";
  if (next.getTime() <= Date.now()) return "Due now";
  return next.toLocaleString();
}

export const NotificationSettings: React.FC = () => {
  const [settings, setSettings] = useState<HitlNotificationSettings | null>(null);
  const [enabledInput, setEnabledInput] = useState(false);
  const [recipientsInput, setRecipientsInput] = useState("");
  const [triggerModeInput, setTriggerModeInput] = useState<HitlNotificationTriggerMode>("scheduled_digest");
  const [frequencyInput, setFrequencyInput] = useState("60");
  const [thresholdInput, setThresholdInput] = useState("5");
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [sendingTest, setSendingTest] = useState(false);

  const loadSettings = useCallback(async (options?: { silent?: boolean }) => {
    try {
      if (!options?.silent) setSettingsError(null);
      const data = await fetchHitlNotificationSettings();
      setSettings((prev) => {
        if (prev === null) {
          setEnabledInput(data.enabled);
          setRecipientsInput(data.recipient_emails.join(", "));
          setTriggerModeInput(data.trigger_mode);
          setFrequencyInput(String(data.digest_frequency_minutes));
          setThresholdInput(String(data.pending_threshold));
        }
        return data;
      });
    } catch (e) {
      if (!options?.silent) {
        setSettingsError(
          e instanceof Error ? e.message : "Failed to load HITL notification settings",
        );
      }
    }
  }, []);

  useEffect(() => {
    void loadSettings();
    const intervalId = window.setInterval(() => void loadSettings({ silent: true }), 5000);
    return () => window.clearInterval(intervalId);
  }, [loadSettings]);

  const parseRecipients = (raw: string): string[] =>
    raw
      .split(/[,;\s]+/)
      .map((part) => part.trim())
      .filter(Boolean);

  const handleSave = async () => {
    const frequency = parseInt(frequencyInput, 10);
    const threshold = parseInt(thresholdInput, 10);
    const recipients = parseRecipients(recipientsInput);

    if (enabledInput && recipients.length === 0) {
      setSettingsError("Enter at least one recipient email when notifications are enabled.");
      return;
    }
    if (
      triggerModeInput === "scheduled_digest" &&
      (!Number.isFinite(frequency) || frequency < 1)
    ) {
      setSettingsError("Digest frequency must be a whole number of minutes (1 or more).");
      return;
    }
    if (
      triggerModeInput === "threshold_only" &&
      (!Number.isFinite(threshold) || threshold < 1)
    ) {
      setSettingsError("Pending threshold must be a whole number (1 or more).");
      return;
    }

    try {
      setSavingSettings(true);
      setSettingsError(null);
      setSettingsMessage(null);
      const data = await saveHitlNotificationSettings({
        enabled: enabledInput,
        recipient_emails: recipients,
        trigger_mode: triggerModeInput,
        digest_frequency_minutes: Number.isFinite(frequency) && frequency >= 1 ? frequency : 60,
        pending_threshold: Number.isFinite(threshold) && threshold >= 1 ? threshold : 5,
      });
      setSettings(data);
      setEnabledInput(data.enabled);
      setRecipientsInput(data.recipient_emails.join(", "));
      setTriggerModeInput(data.trigger_mode);
      setFrequencyInput(String(data.digest_frequency_minutes));
      setThresholdInput(String(data.pending_threshold));
      setSettingsMessage("HITL notification settings saved.");
    } catch (e) {
      setSettingsError(e instanceof Error ? e.message : "Failed to save notification settings");
    } finally {
      setSavingSettings(false);
    }
  };

  const handleSendTest = async () => {
    try {
      setSendingTest(true);
      setSettingsError(null);
      setSettingsMessage(null);
      const result = await sendHitlNotificationTest();
      setSettingsMessage(result.message);
    } catch (e) {
      setSettingsError(e instanceof Error ? e.message : "Failed to send test email");
    } finally {
      setSendingTest(false);
    }
  };

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <h2>HITL Email Notifications</h2>
          <p>
            Configure email alerts when invoices are flagged for human review (HITL pending).
          </p>
        </div>
        <Link to="/settings" className="button-link">
          ← Back to Settings
        </Link>
      </div>

      <section className="panel-section erp-settings-section">
        <div className="panel-section-main">
          {settings && !settings.smtp_configured && (
            <div className="alert">
              SMTP is not configured yet — set SMTP_HOST and SMTP_FROM in your <code>.env</code>{" "}
              file before emails can be sent.
            </div>
          )}
          {settingsError && <div className="alert alert-error">{settingsError}</div>}
          {settingsMessage && !settingsError && <div className="alert">{settingsMessage}</div>}

          {settings && (
            <div className="erp-settings-sync-times">
              <span className="erp-settings-last-synced">
                HITL pending now: {settings.hitl_pending_count}
              </span>
              <span className="erp-settings-last-synced">
                Last email sent: {formatTimestamp(settings.last_sent_at)}
                {settings.last_pending_count > 0 && (
                  <> ({settings.last_pending_count} pending at send time)</>
                )}
              </span>
              <span className="erp-settings-next-synced">
                Next digest: {formatNextDigest(settings)}
              </span>
            </div>
          )}

          <label className="erp-settings-radio notification-settings-enable">
            <input
              type="checkbox"
              checked={enabledInput}
              onChange={(e) => setEnabledInput(e.target.checked)}
            />
            <span>
              <strong>Enable HITL email notifications</strong>
            </span>
          </label>

          <div className="notification-settings-field">
            <label htmlFor="hitl-recipients">Recipient email(s)</label>
            <input
              id="hitl-recipients"
              type="text"
              className="notification-settings-recipient-input"
              value={recipientsInput}
              onChange={(e) => setRecipientsInput(e.target.value)}
              placeholder="ap-team@company.com, finance@company.com"
              disabled={!enabledInput}
            />
            <p className="search-hint">Separate multiple addresses with commas.</p>
          </div>

          <div className="erp-settings-grid">
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="hitl-trigger-mode"
                value="immediate"
                checked={triggerModeInput === "immediate"}
                onChange={() => setTriggerModeInput("immediate")}
                disabled={!enabledInput}
              />
              <span>
                <strong>Immediate</strong> — send an email as soon as an invoice enters HITL
                pending review. Each email includes the timestamp and current pending count.
              </span>
            </label>
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="hitl-trigger-mode"
                value="scheduled_digest"
                checked={triggerModeInput === "scheduled_digest"}
                onChange={() => setTriggerModeInput("scheduled_digest")}
                disabled={!enabledInput}
              />
              <span>
                <strong>Scheduled digest</strong> — send a summary every
                <input
                  type="number"
                  min={1}
                  max={1440}
                  step={1}
                  className="erp-settings-frequency-input"
                  value={frequencyInput}
                  onChange={(e) => setFrequencyInput(e.target.value)}
                  onFocus={() => setTriggerModeInput("scheduled_digest")}
                  disabled={!enabledInput || triggerModeInput !== "scheduled_digest"}
                  aria-label="Digest frequency in minutes"
                />
                minutes while at least one invoice is pending HITL review.
              </span>
            </label>
            <label className="erp-settings-radio">
              <input
                type="radio"
                name="hitl-trigger-mode"
                value="threshold_only"
                checked={triggerModeInput === "threshold_only"}
                onChange={() => setTriggerModeInput("threshold_only")}
                disabled={!enabledInput}
              />
              <span>
                <strong>Threshold only</strong> — send once when pending count reaches
                <input
                  type="number"
                  min={1}
                  step={1}
                  className="erp-settings-frequency-input"
                  value={thresholdInput}
                  onChange={(e) => setThresholdInput(e.target.value)}
                  onFocus={() => setTriggerModeInput("threshold_only")}
                  disabled={!enabledInput || triggerModeInput !== "threshold_only"}
                  aria-label="Pending threshold count"
                />
                or more; reset when the queue drops below that number.
              </span>
            </label>
          </div>

          <div className="erp-settings-actions">
            <button type="button" onClick={() => void handleSave()} disabled={savingSettings}>
              {savingSettings ? "Saving…" : "Save Settings"}
            </button>
            <button
              type="button"
              onClick={() => void handleSendTest()}
              disabled={sendingTest || !settings?.smtp_configured}
            >
              {sendingTest ? "Sending…" : "Send test email"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
};
