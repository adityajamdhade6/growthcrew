import type { NextConfig } from "next";

// The browser only ever talks to this app; /api is proxied to the FastAPI backend.
const API_URL = process.env.GROWTHCREW_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default config;
