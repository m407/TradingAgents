<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

## Quality gates

- Run `mise run check` before completing a change; CI runs the same command.
- Generate `uv.lock` with `uv lock`; `uv lock --check` enforces its freshness.
- Keep lint rules enabled. Exceptions require human approval, a specific finding,
  and a reason; secret exceptions also need an enforced review date.
