import { build } from "esbuild";
import { mkdir, readdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";

await mkdir(".cache/tests", { recursive: true });
// Component suites are standalone; microphone.test.ts is imported by recovery.test.tsx.
const suites = (await readdir("tests")).filter((name) => name.endsWith(".test.tsx")).sort();
if (!suites.length) throw new Error("No component test suites found");
await build({ entryPoints: suites.map((name) => `tests/${name}`), outdir: ".cache/tests", outExtension: { ".js": ".cjs" },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic",
  packages: "external", define: { "import.meta.env.DEV": "true", "import.meta.env.VITE_INPUT_MAX_LENGTH": "undefined", "import.meta.env.VITE_API_BASE_URL": '""', "import.meta.env.VITE_AUDIO_TIMEOUT_MS": "undefined" },
});
const compiledSuites = suites.map((name) => `.cache/tests/${name.replace(/\.tsx$/, ".cjs")}`);
const result = spawnSync(process.execPath, ["--test", ...compiledSuites], { stdio: "inherit" });
process.exitCode = result.status ?? 1;
