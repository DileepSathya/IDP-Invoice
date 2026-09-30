export const settingsNavigation = [
  { id: "ai", label: "AI", to: "/settings" },
  { id: "licensing", label: "Licensing", to: "/settings/licensing" },
  {
    id: "notifications",
    label: "Notification Service",
    children: [
      { id: "notification-sender-email", label: "Configure Sender Email", to: "/settings/notifications/sender-email" },
      { id: "notifications", label: "Configure Receiver Service", to: "/settings/notifications" },
    ],
  },
  {
    id: "erp",
    label: "ERP Settings",
    children: [
      { id: "company", label: "Company Details", to: "/settings/company" },
      { id: "ledger", label: "Ledger Settings", to: "/settings/ledger" },
      { id: "tally-masters", label: "Tally Master Data", to: "/settings/tally-masters" },
    ],
  },
];

export function settingsSectionForPath(pathname) {
  if (pathname === "/settings" || pathname === "/settings/") return "ai";
  for (const item of settingsNavigation) {
    if (item.to === pathname) return item.id;
    const child = item.children?.find((candidate) => candidate.to === pathname);
    if (child) return child.id;
  }
  return "ai";
}
