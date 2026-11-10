import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, writeFile, unlink, rmdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = fileURLToPath(new URL("../", import.meta.url));
const python = process.env.API_TYPES_PYTHON || "python";
const exported = spawnSync(python, ["-c", "import json; from app.main import app; print(json.dumps(app.openapi()))"], {
  cwd: resolve(frontend, "../backend"), env: { ...process.env, APP_ENV: "development" }, encoding: "utf8",
});
if (exported.error || exported.status !== 0) {
  throw new Error("OpenAPI export failed. Install backend requirements and set API_TYPES_PYTHON if needed.");
}
const schema = JSON.parse(exported.stdout);
const directory = await mkdtemp(join(tmpdir(), "echobill-api-types-"));
const input = join(directory, "openapi.json");
const output = join(directory, "generated.ts");
try {
  await writeFile(input, JSON.stringify(schema));
  const generated = spawnSync(process.execPath, [resolve(frontend, "node_modules/openapi-typescript/bin/cli.js"), input, "-o", output], { encoding: "utf8" });
  if (generated.error || generated.status !== 0) throw new Error("OpenAPI TypeScript generation failed.");
  const normalize = (text) => text.replace(/\r\n/g, "\n");
  const current = resolve(frontend, "src/types/generated.ts");
  const fresh = await readFile(output, "utf8");
  if (process.argv.includes("--write")) {
    await writeFile(current, fresh);
    console.log("Regenerated API types from the checked-out backend.");
  } else if (normalize(await readFile(current, "utf8")) !== normalize(fresh)) {
    throw new Error("API types are stale. Run npm run api:types:local, review the diff, and commit generated.ts.");
  } else console.log("API types match the checked-out backend.");
} finally {
  await unlink(input).catch((error) => { if (error.code !== "ENOENT") throw error; });
  await unlink(output).catch((error) => { if (error.code !== "ENOENT") throw error; });
  await rmdir(directory);
}
