// All documentation remains readable without JavaScript. These controls enhance it.
const copyStatus = document.querySelector("#copy-status");
const pageContents = document.querySelector(".page-contents");
if (pageContents && matchMedia("(min-width: 801px)").matches)
  pageContents.open = true;
for (const link of document.querySelectorAll('a[href="#install-command"]')) {
  link.addEventListener("click", () => {
    document.querySelector("#install-command").open = true;
  });
}
for (const pre of document.querySelectorAll("pre")) {
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", "Code example");
  const code = pre.querySelector("code");
  if (!code || !navigator.clipboard) continue;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = "Copy";
  button.setAttribute("aria-label", "Copy code example");
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = "Copied";
      copyStatus.textContent = "Code copied to clipboard.";
    } catch {
      button.textContent = "Select code";
      const selection = getSelection();
      const range = document.createRange();
      range.selectNodeContents(code);
      selection.removeAllRanges();
      selection.addRange(range);
      copyStatus.textContent =
        "Clipboard unavailable. Code selected; use your browser’s copy command.";
    }
    setTimeout(() => {
      button.textContent = "Copy";
    }, 2200);
  });
  (pre.closest(".code-block,.result-output") || pre).append(button);
}

const choices = [...document.querySelectorAll("[data-example]")];
function showExample(name) {
  choices.forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.example === name),
    ),
  );
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
}
choices.forEach((button) =>
  button.addEventListener("click", () => showExample(button.dataset.example)),
);
if (choices.length) showExample("local");

const search = document.querySelector("#command-search");
if (search) {
  search.disabled = false;
  const rows = [...document.querySelectorAll(".command-table tbody tr")];
  search.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    let matches = 0;
    rows.forEach((row) => {
      row.hidden = !row.textContent.toLowerCase().includes(query);
      if (!row.hidden) matches++;
    });
    document.querySelector("#search-empty").hidden = matches !== 0;
    document.querySelector("#search-status").textContent =
      `${matches} command${matches === 1 ? "" : "s"} found.`;
  });
}

if ("IntersectionObserver" in window) {
  const links = [...document.querySelectorAll(".overview-nav nav a")];
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        links.forEach((link) => {
          if (link.hash === `#${entry.target.id}`)
            link.setAttribute("aria-current", "location");
          else link.removeAttribute("aria-current");
        });
      }
    },
    { rootMargin: "-15% 0px -65% 0px" },
  );
  document
    .querySelectorAll(".overview-content section[id]")
    .forEach((section) => observer.observe(section));
}
