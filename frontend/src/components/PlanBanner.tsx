import React from "react";
import { Link } from "react-router-dom";
import type { LicenseProfile } from "../api";

type PlanBannerProps = {
  profile: LicenseProfile;
};

export const PlanBanner: React.FC<PlanBannerProps> = ({ profile }) => {
  const isTimeBased = profile.plan === "monthly" || profile.plan === "yearly";
  const isQuota = profile.plan === "quota";

  if (!isTimeBased && !isQuota) {
    return null;
  }

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
      <Link to="/settings" className="plan-banner-link">
        View plan details
      </Link>
    </div>
  );
};
