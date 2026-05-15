import type { Config } from "tailwindcss";

// Axiom Foundation design tokens, mirrored from
// axiom-foundation.org/packages/ui/src/tokens.
// Same hex values and the same WCAG-anchored vocabulary.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#faf9f6",
        "paper-elev": "#ffffff",
        ink: "#1c1917",
        "ink-secondary": "#57534e",
        "ink-muted": "#78716c",
        rule: "#e7e5e4",
        "rule-subtle": "#f5f5f4",
        "rule-strong": "#78716c",
        accent: "#92400e",
        "accent-hover": "#7c2d12",
        "accent-light": "rgba(146, 64, 14, 0.06)",
        "code-bg": "#1c1917",
        "code-text": "#e7e5e4",
        success: "#166534",
        warning: "#92400e",
        error: "#991b1b",
      },
      fontFamily: {
        sans: ["var(--f-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--f-serif)", "ui-serif", "Georgia", "serif"],
        mono: ["var(--f-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: {
        eyebrow: "0.16em",
      },
    },
  },
  plugins: [],
};

export default config;
