import type { LicenseProfile } from "../api";

/** Short label for header chip (always visible). */
export function licenseCompactLabel(profile: LicenseProfile): string {
  if (profile.licensed === false) {
    return "No license";
  }
  if (profile.plan === "monthly" || profile.plan === "yearly") {
    if (profile.remainingDays != null) {
      const dayWord = profile.remainingDays === 1 ? "day" : "days";
      return `${profile.remainingDays} ${dayWord} left`;
    }
  }
  if (profile.plan === "quota" && profile.invoicesRemaining != null) {
    return `${profile.invoicesRemaining} invoices left`;
  }
  if (profile.plan === "dev") {
    return "Dev mode";
  }
  if (profile.plan === "onetime") {
    return "Licensed";
  }
  return profile.statusMessage || profile.planLabel;
}

/** Whether the full-width plan banner has content to show. */
export function shouldShowFullPlanBanner(profile: LicenseProfile): boolean {
  if (profile.licensed === false) {
    return true;
  }
  return profile.plan === "monthly" || profile.plan === "yearly" || profile.plan === "quota";
}

export function fullPlanBannerRoute(pathname: string): boolean {
  return pathname === "/" || pathname === "/settings/licensing";
}
