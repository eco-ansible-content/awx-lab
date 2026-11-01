// Validate the generated artifact, including links between the Markdown guides.
import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "_site");
const files = (await readdir(root)).filter((file) => file.endsWith(".html"));
const documents = new Map(
  await Promise.all(
    files.map(async (file) => [
      file,
      await readFile(resolve(root, file), "utf8"),
    ]),
  ),
);
const errors = [];
for (const [file, html] of documents) {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  if (new Set(ids).size !== ids.length) errors.push(`${file}: duplicate IDs`);
  if ((html.match(/<h1\b/g) || []).length !== 1)
    errors.push(`${file}: expected one h1`);
  for (const match of html
    .replace(/<base\b[^>]*>/g, "")
    .matchAll(/\b(?:href|src)="([^"]+)"/g)) {
    const href = match[1];
    if (/^(?:[a-z]+:|\/\/)/i.test(href)) continue;
    const [path, fragment] = href.split("#");
    const target = path || file;
    try {
      await stat(resolve(root, target));
    } catch {
      errors.push(`${file}: missing ${href}`);
      continue;
    }
    if (fragment && extname(target) === ".html") {
      const targetHtml = documents.get(target);
      if (!targetHtml?.includes(`id="${fragment}"`))
        errors.push(`${file}: missing anchor ${href}`);
    }
  }
  for (const image of html.matchAll(/<img\b[^>]*>/g)) {
    if (!/\balt="[^"]+"/.test(image[0]))
      errors.push(`${file}: image missing descriptive alt`);
  }
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    `Checked ${files.length} pages: local links, anchors, images, headings and IDs pass.`,
  );
