import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchLicenseProfile, logoutDashboard, type LicenseProfile } from "../api";

function formatPlanType(plan: string): string {
  const labels: Record<string, string> = {
    monthly: "Monthly (time-based)", yearly: "Yearly (time-based)",
    quota: "Quota (invoice count)", onetime: "One-time (unlimited)", dev: "Development",
  };
  return labels[plan] ?? plan;
}

export const AccountMenu: React.FC = () => {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<LicenseProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const toggleOpen = () => {
    setOpen((current) => {
      const next = !current;
      if (next && !profile) {
        setError(null);
        void fetchLicenseProfile().then(setProfile).catch((reason) => {
          setError(reason instanceof Error ? reason.message : "Failed to load profile");
        });
      }
      return next;
    });
  };

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const goToSettings = () => {
    setOpen(false);
    navigate("/settings");
  };

  const handleLogout = async () => {
    try { await logoutDashboard(); }
    finally {
      setOpen(false);
      navigate("/login", { replace: true });
    }
  };

  return (
    <div className="account-menu" ref={menuRef}>
      <button type="button" className="account-menu-trigger" onClick={toggleOpen} aria-haspopup="menu" aria-expanded={open} aria-label="Account menu">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <circle cx="12" cy="8" r="4" fill="currentColor" />
          <path d="M4 20c0-4.418 3.582-7 8-7s8 2.582 8 7" fill="currentColor" />
        </svg>
      </button>
      {open && (
        <div className="account-menu-panel" role="menu">
          <div className="account-menu-section">
            <h4 className="account-menu-pane-title">Profile</h4>
            {error && <div className="alert alert-error">{error}</div>}
            {!error && !profile && <p className="settings-loading">Loading…</p>}
            {profile && (
              <div className="account-profile-card">
                <div className="account-profile-row"><span className="account-profile-label">Account</span><span className="account-profile-value">{profile.customerId || "—"}</span></div>
                <div className="account-profile-row"><span className="account-profile-label">Plan</span><span className="account-profile-value">{profile.planLabel} · {formatPlanType(profile.plan)}</span></div>
                <div className="account-profile-row"><span className="account-profile-label">Status</span><span className="account-profile-value">{profile.statusMessage}</span></div>
              </div>
            )}
          </div>
          <div className="account-menu-tabs account-menu-tabs-stacked account-menu-actions">
            <button type="button" className="account-menu-tab account-menu-tab-link" onClick={goToSettings}>
              Settings <span className="account-menu-tab-arrow" aria-hidden="true">→</span>
            </button>
            <button type="button" className="account-menu-tab account-menu-tab-link account-menu-logout" onClick={() => void handleLogout()}>Log out</button>
          </div>
        </div>
      )}
    </div>
  );
};
