import React, { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { settingsNavigation, settingsSectionForPath } from "../settingsNavigation.mjs";

export const SettingsLayout: React.FC = () => {
  const location = useLocation();
  const activeSection = settingsSectionForPath(location.pathname);
  const erpActive = ["company", "ledger", "tally-masters"].includes(activeSection);
  const notificationsActive = ["notifications", "notification-sender-email"].includes(activeSection);
  const [erpExpanded, setErpExpanded] = useState(erpActive);
  const [notificationsExpanded, setNotificationsExpanded] = useState(notificationsActive);

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
              <SettingsNavGroup
                key={item.id}
                item={item}
                expanded={item.id === "erp" ? erpExpanded : notificationsExpanded}
                onToggle={() => {
                  if (item.id === "erp") setErpExpanded((expanded) => !expanded);
                  else setNotificationsExpanded((expanded) => !expanded);
                }}
              />
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

type SettingsNavGroupProps = {
  item: { id: string; label: string; children: { id: string; label: string; to?: string }[] };
  expanded: boolean;
  onToggle: () => void;
};

const SettingsNavGroup: React.FC<SettingsNavGroupProps> = ({ item, expanded, onToggle }) => (
  <div className="settings-nav-group">
    <button
      type="button"
      className="settings-nav-link settings-nav-group-toggle"
      aria-expanded={expanded}
      onClick={onToggle}
    >
      <span>{item.label}</span>
      <span aria-hidden="true">{expanded ? "−" : "+"}</span>
    </button>
    {expanded && (
      <div className="settings-nav-children">
        {item.children.map((child) => (
          <NavLink key={child.id} to={child.to ?? "/settings"} end className="settings-nav-link">
            {child.label}
          </NavLink>
        ))}
      </div>
    )}
  </div>
);
