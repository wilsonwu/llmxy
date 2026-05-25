import type { Config } from "tailwindcss";
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: { extend: { colors: { brand: { 600: "#0f766e", 700: "#0e6b65" } } } },
  plugins: [],
};
export default config;
