import { build } from "esbuild";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

// IIFE assets work with Qt's file:// origin and the exported browser preview.
// Outputs are generated, ignored package resources; source edits live in src/web/.
const output = new URL(
  "../python/nrclip_builder/resources/map/",
  import.meta.url,
);
await mkdir(output, { recursive: true });
await build({
  entryPoints: [fileURLToPath(new URL("src/main.tsx", import.meta.url))],
  outfile: fileURLToPath(new URL("app.js", output)),
  bundle: true,
  format: "iife",
  jsx: "automatic",
  target: "es2022",
  minify: true,
  legalComments: "inline",
  loader: { ".png": "dataurl" },
  define: { "process.env.NODE_ENV": '"production"' },
});
await copyFile(
  new URL("index.html", import.meta.url),
  new URL("index.html", output),
);
const notices = [];
for (const [packageName, license] of [
  ["react", "LICENSE"],
  ["react-dom", "LICENSE"],
  ["scheduler", "LICENSE"],
  ["leaflet", "LICENSE"],
]) {
  notices.push(
    packageName +
      "\n\n" +
      (await readFile(
        new URL(`node_modules/${packageName}/${license}`, import.meta.url),
        "utf8",
      )),
  );
}
await writeFile(
  new URL("LICENSES.txt", output),
  notices.join("\n\n-----\n\n"),
  "utf8",
);
