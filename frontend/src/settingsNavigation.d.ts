export type SettingsNavigationItem = {
  id: string;
  label: string;
  to?: string;
  children?: SettingsNavigationItem[];
};

export const settingsNavigation: SettingsNavigationItem[];
export function settingsSectionForPath(pathname: string): string;
