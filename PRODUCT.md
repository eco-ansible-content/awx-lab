# AWX Lab

AWX Lab gives AWX and awx-plugins engineers a reusable local environment for
testing PR merges or saved source changes before opening a PR. It builds images,
deploys through kind and AWX Operator, runs basic checks, and hands the running
application to the engineer for manual feature testing.

Targets are macOS Apple Silicon and Linux x86_64 with Podman or Docker. The
repository's validation record distinguishes implemented paths from completed
live acceptance. The CLI is Python 3.11+, with no third-party runtime packages.

The existing terminal identity is the AWX LAB block wordmark, cyan accent and
“BUILD · TEST · REPEAT” tagline. The interactive console accepts AWX Lab commands
only and shows detailed logs alongside concise output. Direct CLI usage remains
supported. `DESIGN.md` owns that native terminal surface.

The website helps teammates understand the tool, install it, select a source,
interpret results and find complete command documentation. Preserve factual
limitations: ready is not full feature validation, local mode uses one checkout,
saved status is not live health, and failed migrations are not auto-rolled back.
Use illustrative terminal previews with explicit captions; do not invent test
evidence, performance claims or features. Documentation should remain readable
without JavaScript. `site/DESIGN.md` owns the website's visual system.
