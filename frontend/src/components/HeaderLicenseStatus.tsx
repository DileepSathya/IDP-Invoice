import React from "react";
import { Link } from "react-router-dom";
import { useLicenseProfile } from "../context/LicenseProfileContext";
import { licenseCompactLabel } from "./licenseDisplay";

export const HeaderLicenseStatus: React.FC = () => {
  const { profile } = useLicenseProfile();
  if (!profile) {
    return null;
  }

  const warning = profile.licensed === false;
  const label = licenseCompactLabel(profile);
  const title = warning
    ? profile.validationError || profile.statusMessage
    : `${profile.planLabel}${profile.customerId ? ` · ${profile.customerId}` : ""} · ${profile.statusMessage}`;

  return (
    <Link
      to="/settings/licensing"
      className={`header-license-chip${warning ? " header-license-chip--warning" : ""}`}
      title={title}
    >
      <span className="header-license-chip-plan">{profile.planLabel}</span>
      <span className="header-license-chip-sep" aria-hidden="true">
        ·
      </span>
      <span className="header-license-chip-status">{label}</span>
    </Link>
  );
};
