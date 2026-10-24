// Runs `next <args>` with the hosted-dashboard settings from cloud.config.json (cross-platform).
// Used by `npm run build:cloud` (Vercel) and `npm run dev:cloud`. Plain `npm run build` stays the local agent build.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const cfg = JSON.parse(readFileSync(join(root, "cloud.config.json"), "utf8"));
const env = {
  ...process.env,
  NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL || cfg.supabaseUrl,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || cfg.supabasePublishableKey,
};
const bin = join(root, "node_modules", "next", "dist", "bin", "next");
const r = spawnSync(process.execPath, [bin, ...process.argv.slice(2)], { stdio: "inherit", env, cwd: root });
process.exit(r.status ?? 1);
