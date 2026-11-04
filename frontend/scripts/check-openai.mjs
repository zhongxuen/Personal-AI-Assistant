// Build gate for the one-client rule (OPENAI-KEY-RULES.md rule 4, local), frontend half.
// Runs as `prebuild`, so every `npm run build` (including Vercel's) fails if frontend
// code imports the `openai` package or calls api.openai.com. The
// frontend never talks to OpenAI at all -- only the backend's guard does
// (backend/app/llm/openai_guard.py; backend gate: backend/scripts/check_openai.py).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const SKIP = new Set(["node_modules", "dist", "build", ".git"]);
const CODE = /\.(ts|tsx|js|jsx|mjs|cjs)$/;
const SELF = "scripts/check-openai.mjs";

const RULES = [
  [/(from\s+|require\(\s*|import\(\s*)['"]openai(\/[^'"]*)?['"]/g, "imports the openai package"],
  [/api\.openai\.com/g, "calls api.openai.com directly"],
];

function* files(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (CODE.test(name)) yield path;
  }
}

const violations = [];
for (const path of files(root)) {
  const rel = relative(root, path).replaceAll("\\", "/");
  if (rel === SELF) continue;
  const text = readFileSync(path, "utf8");
  for (const [pattern, reason] of RULES) {
    for (const match of text.matchAll(pattern)) {
      const line = text.slice(0, match.index).split("\n").length;
      violations.push(`${rel}:${line}: ${reason}`);
    }
  }
}

if (violations.length) {
  console.error("OpenAI one-client check FAILED (see OPENAI-KEY-RULES.md rule 4):");
  for (const v of violations) console.error(`  ${v}`);
  process.exit(1);
}
console.log("OpenAI one-client check passed.");
