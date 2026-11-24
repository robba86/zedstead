---
name: readme-docs
description: Keeps README.md in step with the user docs on zedstead.com (the zedstead-site repo, src/docs/ and src/data/docs.js). Use after a change in zedstead-site's docs (a page added, removed, renamed or reordered, a heading renamed, an index blurb changed), before a commit that touches README.md's links or its Documentation table, and when asked to update the README for the docs.
---

# README and the zedstead.com docs

README.md links the user docs by URL (`https://zedstead.com/docs/<slug>/#<anchor>`). The docs live in the `zedstead-site` repo, which this repo finds at `$ZSM_SITE` or `../zedstead-site` (as `tests/conftest.py::site_doc` does). When a page or a heading there changes, a README link can break, and nothing in the site build tells you.

## Where the facts come from

1. `src/data/docs.js` in zedstead-site: `DOC_ORDER` is the one list of pages and their order.
2. `src/docs/<slug>.md`: the page. Its `#` line is the title, and its `##`…`######` headings give the anchors (GitHub's ids, `slugger()` in `docs.js`).
3. `src/locales/en.json`, `docs.items.<slug>`: the blurb on the site's docs index.

## What the README must hold

1. The Documentation table has one row per slug in `DOC_ORDER`, in the same order.
2. Each row's text is the page's `docs.items` blurb without the final period. A command in the blurb can go in backticks (for example `` `zsm service firewall` `` for "the firewall").
3. The row's link label is a short name for the page, not its full `#` title.
4. Every `https://zedstead.com/docs/...` link in the README resolves to a page and, if it has one, a heading.
5. A fact the README repeats from a doc (tested systems, install commands, the default port 8433, Uninstall) matches that doc. Read the doc's section when its commit touched it.
6. The README calls the web UI "the app", never "the panel", as the site does. File names such as `docs/media/panel.jpg` stay.

## Steps

1. Run the check from the repo root: `.venv/bin/python .claude/skills/readme-docs/check_readme_docs.py`. It exits 1 on a broken link or a table that does not match `DOC_ORDER`, and prints a `note:` when a blurb differs from `en.json`.
2. List the site's doc commits since the README last changed: `git log -1 --format=%cI -- README.md` here, then `git -C ../zedstead-site log --since=<that date> --stat -- src/docs src/data/docs.js src/locales/en.json`.
3. Read those diffs. Look for renamed headings (a moved anchor), new or removed pages, and changed facts from rule 5 above.
4. Edit README.md. Keep the wording rules of the root `CLAUDE.md` and the brand rule: Zedstead in prose, `zsm` for the command.
5. Run the check again until it prints `OK`. A `note:` that you kept on purpose (rule 2 above) is fine.
6. README.md is not something a Zedstead user runs, so it needs no version bump (see the `versioning` skill). Commit it by pathspec as `.claude/CONVENTIONS.md` says.

## Limits

1. The check reads the zedstead-site working tree, not what zedstead.com serves. A site change that is not deployed yet still passes.
2. The check does not compare prose facts (rule 5). That part is a read.
3. On the maintainer's machine, a PostToolUse hook in zedstead-site's `.claude/settings.local.json` runs the check after an Edit or Write of a doc file there, and reports a failure to that session. An edit made with a shell command does not trigger it.
