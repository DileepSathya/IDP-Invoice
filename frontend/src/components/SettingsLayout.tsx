import React, { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { settingsNavigation, settingsSectionForPath } from "../settingsNavigation.mjs";

export const SettingsLayout: React.FC = () => {
  const location = useLocation();
  const activeSection = settingsSectionForPath(location.pathname);
  const erpActive = ["company", "ledger", "tally-masters"].includes(activeSection);
  const [erpExpanded, setErpExpanded] = useState(erpActive);

  return (
    <div className="panel settings-panel">
      <div className="settings-heading">
        <h2>Settings</h2>
        <p>Manage AI, notifications, and ERP integrations in one place.</p>
      </div>
      <div className="settings-layout">
        <aside className="settings-sidebar" aria-label="Settings navigation">
          {settingsNavigation.map((item) =>
            item.children ? (
              <div key={item.id} className="settings-nav-group">
                <button
                  type="button"
                  className={`settings-nav-link settings-nav-group-toggle${erpActive ? " is-active" : ""}`}
                  aria-expanded={erpExpanded}
                  onClick={() => setErpExpanded((expanded) => !expanded)}
                >
                  <span>{item.label}</span>
                  <span aria-hidden="true">{erpExpanded ? "−" : "+"}</span>
                </button>
                {erpExpanded && (
                  <div className="settings-nav-children">
                    {item.children.map((child) => (
                      <NavLink key={child.id} to={child.to ?? "/settings"} className="settings-nav-link">
                        {child.label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <NavLink key={item.id} to={item.to ?? "/settings"} end={item.to === "/settings"} className="settings-nav-link">
                {item.label}
              </NavLink>
            ),
          )}
        </aside>
        <div className="settings-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
};
