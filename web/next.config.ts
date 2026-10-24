import type { NextConfig } from "next";

// Production: static export served by the home PC agent (same origin as /api, /dav, /mcp).
// Development: `npm run dev` proxies API calls to the agent on 127.0.0.1:8787.
const isProd = process.env.NODE_ENV === "production";
const agent = process.env.PCLOUD_AGENT_URL || "http://127.0.0.1:8787";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  ...(isProd
    ? { output: "export" as const }
    : {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${agent}/api/:path*` },
            { source: "/dav/:path*", destination: `${agent}/dav/:path*` },
          ];
        },
      }),
};

export default config;
