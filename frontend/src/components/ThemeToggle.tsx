import { MonitorIcon, MoonIcon, SunIcon } from "@phosphor-icons/react";
import { setThemePreference, useThemePreference, type ThemePreference } from "../theme";

const CYCLE: ThemePreference[] = ["system", "light", "dark"];

const META: Record<ThemePreference, { label: string; Icon: typeof SunIcon }> = {
  system: { label: "System", Icon: MonitorIcon },
  light: { label: "Light", Icon: SunIcon },
  dark: { label: "Dark", Icon: MoonIcon },
};

/** Single button cycling system → light → dark. The icon shows the current mode. */
export function ThemeToggle() {
  const preference = useThemePreference();
  const next = CYCLE[(CYCLE.indexOf(preference) + 1) % CYCLE.length];
  const { label, Icon } = META[preference];
  return (
    <button
      type="button"
      className="icon-btn theme-btn"
      title={`Theme: ${label}. Switch to ${META[next].label.toLowerCase()}`}
      aria-label={`Theme: ${label}. Switch to ${META[next].label.toLowerCase()}`}
      onClick={() => setThemePreference(next)}
    >
      <Icon size={15} weight="duotone" />
    </button>
  );
}
