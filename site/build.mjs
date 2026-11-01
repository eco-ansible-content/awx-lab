import { mkdir, readFile, writeFile, cp, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";

const root = dirname(fileURLToPath(import.meta.url));
const repo = resolve(root, "..");
const out = resolve(root, "_site");
const version = (
  await readFile(resolve(repo, "awx_lab/__init__.py"), "utf8")
).match(/__version__\s*=\s*["']([^"']+)/)[1];
const repository = "https://github.com/eco-ansible-content/awx-lab";
const pages = [
  [
    "user-guide.md",
    "guide.html",
    "User guide",
    "Installation, every command and option, storage, reports and troubleshooting.",
  ],
  [
    "terminal-interface.md",
    "terminal.html",
    "Terminal guide",
    "Commands on the left. Detailed logs on the right. Make the console your daily workspace.",
  ],
  [
    "design.md",
    "architecture.html",
    "How it works",
    "The build pipeline, source selection and the handoff to manual testing.",
  ],
  [
    "validation.md",
    "validation.html",
    "Validation record",
    "Completed checks, observed failures and acceptance work still to do.",
  ],
  [
    "backlog.md",
    "backlog.html",
    "Project backlog",
    "The project’s current backlog, maintained alongside the code.",
  ],
];
const escape = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const arrow =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>';
const nav = `<header class="site-header"><div class="header-inner"><a class="brand" href="index.html" aria-label="AWX Lab home">AWX<span>LAB</span></a><span class="version">v${version}</span><nav aria-label="Main navigation"><a href="index.html#start">Get started</a><a href="index.html#examples">Examples</a><a href="guide.html">User guide</a><a class="github-link" href="${repository}">GitHub ${arrow}</a></nav></div></header>`;
const footer = `<footer class="site-footer"><a class="brand" href="index.html">AWX<span>LAB</span></a><p>Build. Test. Repeat.<br><span>A local development tool for AWX and awx-plugins.</span></p><div><a href="guide.html">Documentation</a><a href="validation.html">Validation record</a><a href="${repository}">Source on GitHub</a><a href="${repository}/issues">Report an issue</a></div><small>AWX Lab v${version} · Community tooling; not an official Ansible product.</small></footer>`;
function shell(title, description, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f5f6f2"><meta name="description" content="${escape(description)}"><meta property="og:title" content="${escape(title)}"><meta property="og:description" content="${escape(description)}"><meta property="og:type" content="website"><title>${escape(title)}</title><link rel="icon" href="assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="assets/style.css"><script src="assets/site.js" defer></script></head><body><a class="skip-link" href="#main">Skip to content</a>${nav}${body}${footer}<p id="copy-status" class="sr-only" role="status"></p></body></html>`;
}
function linkPath(href) {
  if (!href || /^(?:[a-z]+:|#|\/)/i.test(href)) return href;
  const [file, hash] = href.split("#");
  const suffix = hash ? `#${hash}` : "";
  const name = file.replace(/^(?:\.\.\/)?(?:docs\/)?/, "");
  const match = pages.find((page) => page[0] === name);
  if (match) return match[1] + suffix;
  if (name === "README.md")
    return `index.html${hash === "quick-start" ? "#start" : suffix}`;
  if (name === "images/terminal-ui.png") return "assets/terminal-ui.png";
  return `${repository}/blob/main/${file.replace(/^(\.\.\/)+/, "")}${suffix}`;
}

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(resolve(root, "assets"), resolve(out, "assets"), { recursive: true });
await cp(
  resolve(repo, "docs/images/terminal-ui.png"),
  resolve(out, "assets/terminal-ui.png"),
);
await writeFile(resolve(out, ".nojekyll"), "");
const home = (await readFile(resolve(root, "index.html"), "utf8")).replaceAll(
  "{{VERSION}}",
  version,
);
await writeFile(
  resolve(out, "index.html"),
  shell(
    "AWX Lab — Build. Test. Repeat.",
    "Build a reusable local AWX environment. Test pull requests or uncommitted changes with kind, AWX Operator and one CLI.",
    home,
  ),
);

for (const [source, target, title, description] of pages) {
  const headings = [];
  const used = new Map();
  const renderer = new marked.Renderer();
  const defaultCode = renderer.code.bind(renderer);
  renderer.code = function (token) {
    if (source === "design.md" && token.lang === "mermaid") {
      return '<figure><a href="assets/workflow.svg"><img src="assets/workflow.svg" width="960" height="510" alt="AWX Lab lifecycle: create or resume, build changes, update the local kind environment, run checks, then stop and retain or purge."></a><figcaption>Build once, update the same lab, then test your feature. Select to enlarge.</figcaption></figure>';
    }
    return defaultCode(token);
  };
  renderer.heading = function ({ tokens, depth, text }) {
    const label = text.replace(/<[^>]*>|[`*_]/g, "").replaceAll("&amp;", "&");
    const base = label
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s-]/gu, "")
      .replace(/\s/g, "-");
    const count = used.get(base) || 0;
    used.set(base, count + 1);
    const id = count ? `${base}-${count}` : base;
    if (depth === 2) headings.push({ id, label });
    return `<h${depth} id="${escape(id)}">${this.parser.parseInline(tokens)}<a class="heading-anchor" href="#${escape(id)}" aria-label="Link to ${escape(label)}">#</a></h${depth}>\n`;
  };
  const markdown = (await readFile(resolve(repo, "docs", source), "utf8"))
    .replace(/^# .*\n/, "")
    .replace(/^## Contents\n[\s\S]*?(?=^## )/m, "");
  const content = marked
    .parse(markdown, {
      renderer,
      walkTokens(token) {
        if (token.type === "link" || token.type === "image")
          token.href = linkPath(token.href);
      },
    })
    .replaceAll(
      "<table>",
      '<div class="table-wrap" tabindex="0" role="region" aria-label="Reference table; scroll horizontally if needed"><table>',
    )
    .replaceAll("</table>", "</table></div>");
  const contents = headings.length
    ? `<details class="page-contents"><summary>On this page</summary><nav aria-label="On this page">${headings.map(({ id, label }) => `<a href="#${escape(id)}">${escape(label)}</a>`).join("")}</nav></details>`
    : "";
  const sidebar = `<aside class="doc-sidebar"><a href="index.html">${arrow} Overview</a><nav aria-label="Documentation">${pages.map(([, path, name]) => `<a href="${path}"${path === target ? ' aria-current="page"' : ""}>${name}</a>`).join("")}</nav>${contents}</aside>`;
  const body = `<main id="main" class="doc-layout">${sidebar}<article class="prose"><div class="doc-title"><h1>${title}</h1><p>${description}</p><a class="source-link" href="${repository}/blob/main/docs/${source}">View the Markdown source ${arrow}</a></div>${content}</article></main>`;
  await writeFile(
    resolve(out, target),
    shell(`${title} — AWX Lab`, description, body),
  );
}
const siteBase = process.env.SITE_BASE_PATH || "/awx-lab/";
if (!/^\/(?:[a-zA-Z0-9_-]+\/)*$/.test(siteBase))
  throw new Error("SITE_BASE_PATH must be an absolute URL path ending in /");
await writeFile(
  resolve(out, "404.html"),
  shell(
    "Page not found — AWX Lab",
    "Find the AWX Lab documentation.",
    '<main id="main" class="not-found"><h1>This page isn’t in the lab.</h1><p>The address may have changed.</p><a class="button" href="index.html">Back to AWX Lab</a></main>',
  ).replace("<head>", `<head><base href="${siteBase}">`),
);
console.log(
  `Built AWX Lab v${version}: overview, ${pages.length} documentation pages and a 404 page in ${out}`,
);
