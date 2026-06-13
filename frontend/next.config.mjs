// SEC: security headers for the deployed dApp. Applied in PRODUCTION only, so local dev
// (and the in-IDE preview iframe, which a DENY/frame-ancestors header would block) is
// unaffected.
//
// NOTE: a full content CSP (script-src / connect-src / style-src) is intentionally deferred —
// it must be tested against WalletConnect, the Alchemy RPC, the IPFS gateway, and Next's
// inline runtime before enabling, or it breaks wallet-connect / on-chain reads. The headers
// below are all safe to ship as-is: `frame-ancestors 'none'` (+ X-Frame-Options) gives the
// clickjacking / wallet-drainer-iframe protection that matters most for a wallet-signing dApp
// without restricting the app's own resource loading.
const isProd = process.env.NODE_ENV === "production";

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), browsing-topics=()",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    if (!isProd) return [];
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
