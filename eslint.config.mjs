import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Agent-tool worktrees may contain their own build output (dist/, etc.)
    // from stale/isolated sessions; never lint into them.
    ".claude/**",
    // Default ignores of eslint-config-next:
    ".next/**",
    ".venv/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".vinext/**",
    ".wrangler/**",
    "backend/**",
    "runtime/**",
    "workspaces/**",
    "node_modules/**",
    "dist/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
