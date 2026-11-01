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
`PAGES_ENABLED` is `true`. Deployment is currently disabled, as requested.
After a suitable plan upgrade, configure **Settings → Pages → Source: GitHub
Actions** and the intended site visibility before setting this variable.
Then dispatch the **Documentation site** workflow. A standard public project
site would use `https://eco-ansible-content.github.io/awx-lab/`; a privately
published site uses the unique address shown in Pages settings. Set repository
variable `SITE_BASE_PATH` to `/` for a site served at its own domain root, or the
actual `/project-name/` prefix when needed.

GitHub Free organizations can publish Pages from public repositories only. For
this private repository, use a plan supporting private-repository Pages. Keeping
the **website itself** private additionally requires GitHub Enterprise Cloud;
a private source repository alone does not make its Pages site private. See
[GitHub's access-control documentation](https://docs.github.com/en/enterprise-cloud@latest/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site).
Do not change the source repository's visibility or publish to a separate public
repository as part of this setup.

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
