import { build } from "esbuild";
import { fileURLToPath } from "node:url";

await build({
  entryPoints: [fileURLToPath(new URL("browser-entry.ts", import.meta.url))],
  bundle: true,
  outfile: fileURLToPath(
    new URL("../../../build/browser-test.js", import.meta.url),
  ),
  format: "iife",
  loader: { ".png": "dataurl" },
});
