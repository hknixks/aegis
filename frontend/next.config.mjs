/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard is a visualization layer only — see this repo's
  // src/aegis/api.py docstring. It never talks to KeeperHub directly;
  // NEXT_PUBLIC_AEGIS_API_URL points at that read-only backend, which is
  // the only server this app ever calls. No KeeperHub credential is ever
  // defined as a Next.js env var, public or private.
  env: {
    NEXT_PUBLIC_AEGIS_API_URL: process.env.NEXT_PUBLIC_AEGIS_API_URL || "http://localhost:8000",
  },
};

export default nextConfig;
