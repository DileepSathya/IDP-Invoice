import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchLicenseProfile, type LicenseProfile } from "../api";

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

function PlanDetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  return (
    <div className="plan-detail-row">
      <span className="plan-detail-label">{label}</span>
      <span className="plan-detail-value">{value}</span>
    </div>
  );
}

export const Settings: React.FC = () => {
  const [profile, setProfile] = useState<LicenseProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchLicenseProfile();
        if (!cancelled) {
          setProfile(data);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load plan details");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isTimeBased = profile?.plan === "monthly" || profile?.plan === "yearly";
  const isQuota = profile?.plan === "quota";

  return (
    <div className="panel">
      <div className="panel-header">
        <div>
          <h2>Settings</h2>
          <p>View your license and subscription details.</p>
        </div>
        <div className="panel-header-actions">
          <Link to="/settings/notifications" className="button-link">
            HITL Email Notifications
          </Link>
          <Link to="/settings/tally-masters" className="button-link">
            Tally Master Data
          </Link>
          <Link to="/settings/ledger" className="button-link">
            Ledger Settings
          </Link>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <section className="panel-section settings-section">
        <h3>Plan</h3>
        {loading && <p className="settings-loading">Loading plan details…</p>}
        {!loading && profile && (
          <div className="plan-details-card">
            <div className="plan-details-header">
              <span className="plan-details-badge">{profile.planLabel}</span>
              <span className="plan-details-status">{profile.statusMessage}</span>
            </div>

            <div className="plan-details-grid">
              <PlanDetailRow label="Plan type" value={formatPlanType(profile.plan)} />
              <PlanDetailRow label="Customer ID" value={profile.customerId} />
              <PlanDetailRow label="Issued at (UTC)" value={profile.issuedAt} />

              {isTimeBased && (
                <>
                  <PlanDetailRow label="Expires on" value={profile.expiresAt} />
                  <PlanDetailRow
                    label="Days remaining"
                    value={
                      profile.remainingDays !== null
                        ? `${profile.remainingDays} ${profile.remainingDays === 1 ? "day" : "days"}`
                        : null
                    }
                  />
                  <PlanDetailRow label="Invoice processing" value="Unlimited during subscription" />
                </>
              )}

              {isQuota && (
                <>
                  <PlanDetailRow label="Invoice limit" value={profile.invoiceLimit} />
                  <PlanDetailRow label="Invoices processed" value={profile.invoicesUsed} />
                  <PlanDetailRow
                    label="Invoices remaining"
                    value={profile.invoicesRemaining}
                  />
                  {profile.expiresAt && (
                    <PlanDetailRow label="Expires on" value={profile.expiresAt} />
                  )}
                </>
              )}

              {profile.plan === "onetime" && (
                <PlanDetailRow label="Invoice processing" value="Unlimited (lifetime)" />
              )}

              {profile.plan === "dev" && (
                <PlanDetailRow
                  label="Note"
                  value="License validation is disabled in development mode."
                />
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
};
