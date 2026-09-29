import React from "react";
import { Link } from "react-router-dom";
import type { LicenseProfile } from "../api";
import { shouldShowFullPlanBanner } from "./licenseDisplay";

type PlanBannerProps = {
  profile: LicenseProfile;
};

export const PlanBanner: React.FC<PlanBannerProps> = ({ profile }) => {
  if (!shouldShowFullPlanBanner(profile)) {
    return null;
  }

  if (profile.licensed === false) {
    return (
      <div className="plan-banner plan-banner--warning">
        <div className="plan-banner-content">
          <span className="plan-banner-label">{profile.planLabel}</span>
          <span className="plan-banner-highlight">
            {profile.validationError || profile.statusMessage}
          </span>
        </div>
        <Link to="/settings/licensing" className="plan-banner-link">
          Add license key
        </Link>
      </div>
    );
  }

  const isTimeBased = profile.plan === "monthly" || profile.plan === "yearly";
  const isQuota = profile.plan === "quota";

  const highlight =
    isTimeBased && profile.remainingDays !== null
      ? `${profile.remainingDays} ${profile.remainingDays === 1 ? "day" : "days"} left`
      : isQuota && profile.invoicesRemaining !== null
        ? `${profile.invoicesRemaining} invoice processing left`
        : profile.statusMessage;

  return (
    <div className="plan-banner">
      <div className="plan-banner-content">
        <span className="plan-banner-label">{profile.planLabel}</span>
        <span className="plan-banner-highlight">{highlight}</span>
        {profile.customerId && (
          <span className="plan-banner-customer">Licensed to {profile.customerId}</span>
        )}
      </div>
      <Link to="/settings/licensing" className="plan-banner-link">
        View plan details
      </Link>
    </div>
  );
};
