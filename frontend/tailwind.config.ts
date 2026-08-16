import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        console: {
          bg: "#0b0d12",
          panel: "#12151c",
          border: "#232833",
          text: "#e7eaf0",
          muted: "#8a92a3",
        },
        risk: {
          safe: "#2fbf71",
          atrisk: "#e0a800",
          high: "#e0672f",
          critical: "#d64545",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
