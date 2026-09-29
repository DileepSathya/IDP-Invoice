import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { fetchLicenseProfile, type LicenseProfile } from "../api";

type LicenseProfileContextValue = {
  profile: LicenseProfile | null;
  refreshLicense: () => Promise<void>;
};

const LicenseProfileContext = createContext<LicenseProfileContextValue | null>(null);

export const LicenseProfileProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [profile, setProfile] = useState<LicenseProfile | null>(null);

  const refreshLicense = useCallback(async () => {
    try {
      setProfile(await fetchLicenseProfile());
    } catch {
      setProfile(null);
    }
  }, []);

  useEffect(() => {
    void refreshLicense();
  }, [refreshLicense]);

  return (
    <LicenseProfileContext.Provider value={{ profile, refreshLicense }}>
      {children}
    </LicenseProfileContext.Provider>
  );
};

export function useLicenseProfile(): LicenseProfileContextValue {
  const ctx = useContext(LicenseProfileContext);
  if (!ctx) {
    throw new Error("useLicenseProfile must be used within LicenseProfileProvider");
  }
  return ctx;
}
