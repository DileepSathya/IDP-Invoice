import React, { useEffect, useState } from "react";
import { fetchAgentSettings, saveAgentSettings, type AgentSettings } from "../api";

const MODEL_LABELS: Record<string, string> = { gemini: "Gemini" };

export const Settings: React.FC = () => {
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [selectedModel, setSelectedModel] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchAgentSettings()
      .then((data) => {
        if (!cancelled) {
          setSettings(data);
          setSelectedModel(data.model || "gemini");
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Failed to load AI settings");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      setMessage(null);
      const data = await saveAgentSettings(selectedModel, apiKey);
      setSettings(data);
      setApiKey("");
      setMessage("AI settings saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to save AI settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-page-content" aria-labelledby="ai-settings-title">
      <div className="settings-content-header">
        <h3 id="ai-settings-title">AI</h3>
        <p>Select the model used for invoice extraction and provide its API key.</p>
      </div>
      {loading && <p className="settings-loading">Loading AI settings…</p>}
      {error && <div className="alert alert-error">{error}</div>}
      {message && !error && <div className="alert">{message}</div>}
      {!loading && (
        <div className="settings-form-card">
          <fieldset className="settings-fieldset">
            <legend>AI model</legend>
            {(settings?.supported_models ?? ["gemini"]).map((model) => (
              <label key={model} className="settings-radio-row">
                <input type="radio" name="ai-model" checked={selectedModel === model} onChange={() => setSelectedModel(model)} />
                <span>{MODEL_LABELS[model] ?? model}</span>
              </label>
            ))}
          </fieldset>
          <label className="settings-form-field" htmlFor="agent-api-key">
            <span>API key</span>
            <input
              id="agent-api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={settings?.api_key_masked ? `Current: ${settings.api_key_masked} (enter to replace)` : "Enter API key"}
              autoComplete="off"
            />
          </label>
          <p className="search-hint">
            {settings?.configured ? "AI is configured and ready." : "Select a model and enter an API key before processing invoices."}
          </p>
          <div className="settings-form-actions">
            <button type="button" onClick={() => void handleSave()} disabled={saving || !apiKey.trim()}>
              {saving ? "Saving…" : "Save AI Settings"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
};
