import React, { useEffect, useRef, useState } from "react";
import {
  AgentSettings,
  LicenseProfile,
  fetchAgentSettings,
  fetchLicenseProfile,
  saveAgentSettings,
} from "../api";

const MODEL_LABELS: Record<string, string> = {
  gemini: "Gemini",
};

function formatPlanType(plan: string): string {
  switch (plan) {
    case "monthly":
      return "Monthly (time-based)";
    case "yearly":
      return "Yearly (time-based)";
    case "quota":
      return "Quota (invoice count)";
    case "onetime":
      return "One-time (unlimited)";
    case "dev":
      return "Development";
    default:
      return plan;
  }
}

export const AccountMenu: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"profile" | "settings">("profile");
  const [profile, setProfile] = useState<LicenseProfile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string>("gemini");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const loadData = async () => {
    try {
      setProfileError(null);
      const data = await fetchLicenseProfile();
      setProfile(data);
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Failed to load profile");
    }
    try {
      setAgentError(null);
      const data = await fetchAgentSettings();
      setAgentSettings(data);
      if (data.model) {
        setSelectedModel(data.model);
      }
    } catch (e) {
      setAgentError(e instanceof Error ? e.message : "Failed to load AI agent settings");
    }
  };

  const toggleOpen = () => {
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        setSaveMessage(null);
        void loadData();
      }
      return next;
    });
  };

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const handleSaveAgentSettings = async () => {
    try {
      setSaving(true);
      setSaveMessage(null);
      setAgentError(null);
      const data = await saveAgentSettings(selectedModel, apiKeyInput);
      setAgentSettings(data);
      setApiKeyInput("");
      setSaveMessage("AI agent settings saved.");
    } catch (e) {
      setAgentError(e instanceof Error ? e.message : "Failed to save AI agent settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="account-menu" ref={menuRef}>
      <button
        type="button"
        className="account-menu-trigger"
        onClick={toggleOpen}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Account menu"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <circle cx="12" cy="8" r="4" fill="currentColor" />
          <path
            d="M4 20c0-4.418 3.582-7 8-7s8 2.582 8 7"
            fill="currentColor"
          />
        </svg>
      </button>

      {open && (
        <div className="account-menu-panel" role="menu">
          <div className="account-menu-tabs">
            <button
              type="button"
              className={`account-menu-tab${activeTab === "profile" ? " is-active" : ""}`}
              onClick={() => setActiveTab("profile")}
            >
              Profile
            </button>
            <button
              type="button"
              className={`account-menu-tab${activeTab === "settings" ? " is-active" : ""}`}
              onClick={() => setActiveTab("settings")}
            >
              Settings
            </button>
          </div>

          {activeTab === "profile" && (
            <div className="account-menu-section">
              {profileError && <div className="alert alert-error">{profileError}</div>}
              {!profileError && !profile && <p className="settings-loading">Loading…</p>}
              {profile && (
                <div className="account-profile-card">
                  <div className="account-profile-row">
                    <span className="account-profile-label">Account</span>
                    <span className="account-profile-value">{profile.customerId || "—"}</span>
                  </div>
                  <div className="account-profile-row">
                    <span className="account-profile-label">Plan</span>
                    <span className="account-profile-value">
                      {profile.planLabel} · {formatPlanType(profile.plan)}
                    </span>
                  </div>
                  <div className="account-profile-row">
                    <span className="account-profile-label">Status</span>
                    <span className="account-profile-value">{profile.statusMessage}</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === "settings" && (
            <div className="account-menu-section">
              <h4 className="account-menu-pane-title">AI agent</h4>
              {agentError && <div className="alert alert-error">{agentError}</div>}
              {saveMessage && <div className="alert">{saveMessage}</div>}

              <div className="account-menu-field-group">
                <span className="account-menu-field-label">AI model</span>
                {(agentSettings?.supported_models ?? ["gemini"]).map((model) => (
                  <label key={model} className="account-menu-radio">
                    <input
                      type="radio"
                      name="ai-model"
                      value={model}
                      checked={selectedModel === model}
                      onChange={() => setSelectedModel(model)}
                    />
                    {MODEL_LABELS[model] ?? model}
                  </label>
                ))}
              </div>

              <label className="account-menu-field-label" htmlFor="agent-api-key">
                API key
              </label>
              <input
                id="agent-api-key"
                type="password"
                className="account-menu-input"
                placeholder={
                  agentSettings?.api_key_masked
                    ? `Current: ${agentSettings.api_key_masked} (enter to replace)`
                    : "Enter API key"
                }
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                autoComplete="off"
              />

              <div className="account-menu-status">
                {agentSettings?.configured ? (
                  <span className="account-menu-status-ok">✓ AI agent configured</span>
                ) : (
                  <span className="account-menu-status-warn">
                    AI model and API key are required before invoices can be uploaded.
                  </span>
                )}
              </div>

              <button
                type="button"
                className="account-menu-save"
                onClick={() => void handleSaveAgentSettings()}
                disabled={saving || !apiKeyInput.trim()}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
