---
name: versioning
description: How Zedstead bumps its version number under semver.org rules (MAJOR.MINOR.PATCH). Use before every commit that changes what a Zedstead user runs (app/, webui/, app/web/static/, install.sh, install.ps1, docker/, the dependencies in pyproject.toml), and when asked about the version, a release or a release tag.
---

# Versioning

## Where the version lives

1. `app/__init__.py` holds `__version__`. This is the only place to change it.
2. `pyproject.toml` reads it from there (`[tool.setuptools.dynamic]`). Never write a version into `pyproject.toml`.
3. `zsm --version`, the panel status, the self-update card and the update check all read `__version__`.
4. `webui/package.json` stays at `0.0.0`. Nothing reads it.

## Format

Write the version as plain `MAJOR.MINOR.PATCH`, for example `0.4.2`. Do not add a `v`, a pre-release part (`-rc.1`) or build metadata (`+abc`). `_parse_version` in `app/shared/version_check.py` reads any other form as 0, and the panel then never shows an update. A GitHub release tag is `v` plus the number, for example `v0.4.2`.

## When to bump

Users update by following the branch (`app/ops/self_update.py`), so every push to `main` is a release for them. The rule is one bump per push, at the highest level that the unpushed commits need.

1. Put the bump in the same commit as the change, never in a commit of its own.
2. Read the pushed version: `git show origin/main:app/__init__.py | grep __version__`.
3. Find the level of the change (next section).
4. If the local version equals the pushed version, bump the local version at that level.
5. If the local version is already higher, an unpushed commit bumped it. Bump again only if this change needs a higher level than that bump. Always count from the pushed version.
6. If a merge conflicts on the `__version__` line, keep the higher version.

Example: the pushed version is `0.3.1`. A fix commit sets `0.3.2`. A second fix keeps `0.3.2`. A feature commit after that sets `0.4.0`, counted from `0.3.1`.

Do not bump for a change that never reaches a user: `tests/`, Markdown files, the `CLAUDE.md` files, `.claude/`, `plans/`, `ROADMAP.md`, developer-only scripts and comments.

## Which level

The public API of Zedstead is what a user or a user's script depends on: CLI commands, flags and exit codes, web API routes, `config.json` and the other files that Zedstead writes, the installer flags, and the Docker environment variables and volumes.

| Level | When | Examples |
|---|---|---|
| MAJOR | An existing install or script stops working after the update, or needs a manual step. | A command, flag, route or config key removed or renamed. A file format changed with no reader for the old one. A platform or Python version dropped. |
| MINOR | New behavior that keeps old behavior working. | A new command, flag, page, editor, map or pre-configured mod. A new config key with a default. |
| PATCH | A fix that changes no interface. | A bug fix, wording, layout, speed, a dependency bump that users do not see. |

1. A MINOR bump sets PATCH to 0: `0.3.2` becomes `0.4.0`.
2. A MAJOR bump sets MINOR and PATCH to 0: `1.4.2` becomes `2.0.0`.

## While MAJOR is 0

semver.org item 4 says that `0.y.z` is initial development. While MAJOR is 0, a breaking change bumps MINOR, and the table applies to the rest.

1. Never move to `1.0.0` on your own. The maintainer decides when.
2. Ask the maintainer before any MAJOR bump. A breaking change usually removes functionality, and the root `CLAUDE.md` requires a question before that anyway.

## Report it

Name the old and the new version in your reply, for example "Claude: bumped 0.3.1 to 0.3.2 (patch: bug fix)". Add `app/__init__.py` to the pathspec of the commit.
