# AWX Lab website

The overview and full documentation build into a static GitHub Pages site. No
server, analytics, external fonts or client framework is required. Node.js is a
website development dependency only; the CLI still needs only Python.

## Preview

With Node.js 22+ and Python available, from the repository root:

```sh
npm ci --prefix site
npm run build --prefix site
npm run check --prefix site
python3 -m http.server 8080 --bind 127.0.0.1 --directory site/_site
```

Open `http://127.0.0.1:8080`. `site/_site` is generated and ignored by Git.
For a different Pages project URL, set `SITE_BASE_PATH=/project-name/` when
building. This sets the base URL for the custom 404 page; normal pages use
portable relative links.

## Content

- `site/index.html`: overview, quick start and interactive examples.
- `docs/user-guide.md`, `terminal-interface.md`, `design.md`, `validation.md`,
  `backlog.md`: rendered into the website at build time, with internal links and
  navigation. Edit these source documents, not the generated pages.
- `awx_lab/__init__.py`: the displayed version.
- `site/assets`: styles, progressive enhancement, fonts and the narrow terminal
  preview. The wide preview is copied from `docs/images/terminal-ui.png`.
- `site/assets/workflow.svg`: the readable lifecycle diagram used in place of
  the architecture guide's Mermaid block. Keep it aligned with that guide.
- `site/check.mjs`: checks generated local links, anchors, headings and images.

## GitHub Pages

The Pages workflow builds and validates on relevant pushes, pull requests and
manual dispatches. It deploys from `main` when repository variable
`PAGES_ENABLED` is `true`. Pages is configured to publish through GitHub Actions
at <https://eco-ansible-content.github.io/awx-lab/>. The repository and site are
public. Changes to the site or rendered documentation deploy after a reviewed
pull request is merged into protected `main`. To redeploy the current commit,
dispatch the **Documentation site** workflow on `main`. Set repository variable
`SITE_BASE_PATH` to `/` for a site served at its own domain root, or the actual
`/project-name/` prefix when needed; the current project path uses the default.

Deployment uploads only `site/_site`. The local AWX environment and saved logs,
credentials, Git checkouts and reports are never website build inputs.

## Asset sources

The terminal previews are illustrative captures of AWX Lab's actual curses
drawing function with synthetic output, not screenshots of successful live
deployments. The narrow image is from the same preview render used for the
terminal release. The wordmark preserves the CLI's AWX/LAB name and cyan accent.

DM Sans is self-hosted under its SIL Open Font License, included in
`site/assets/fonts/OFL.txt`. Source:
https://github.com/google/fonts/tree/main/ofl/dmsans
