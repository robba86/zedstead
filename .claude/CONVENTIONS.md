# Working conventions for this repo

Not auto-loaded. **Read this file before your first commit in a session, and before
changing code that looks load-bearing.** Root `CLAUDE.md` points here.

---

## Committing

**Stage only the files this session touched.** Never `git add -A` / `git add .` / `git commit -a`. The working tree can hold uncommitted changes by someone else, and their owner commits them separately. A blanket add sweeps their work into your commit.

**Procedure: name your paths on the commit itself.**

```
git commit -F <message-file> -- <path> …
```

The pathspec form commits exactly those paths from the working tree and ignores
whatever else is in the index, so nothing else can join your commit. Afterwards
`git status --short` to confirm other files are untouched.

A new file is not known to git yet, so the pathspec form refuses it with "did not match any file(s) known to git". Add each new file by name in the same command line: `git add -- <new path> … && git commit -F <message-file> -- <path> …`.

**Do not `git reset -q` first.** It clears the *entire* index, including files someone
else already staged, and a later broad `git add` then sweeps them in. If you need a
reset, narrow it to your own paths: `git reset -q -- <path> …`.

Recovery, if a commit picked up other files and is unpushed:

```
git log -1 --format=%B <sha> > /tmp/msg.txt
git reset --soft HEAD~1 && git reset -q -- <their paths> && git commit -F /tmp/msg.txt -- <your paths>
```

`--soft` leaves every working-tree file untouched, and keeping it to one invocation
closes the window again.

**Check the branch before your first commit** (`git branch --show-current`). Someone
else working in the same worktree can have switched it.

**An unpushed sha can stop existing.** A commit that gets re-made has a new hash, so
`git log` before anyone acts on a sha you quoted.

**Bump the version in the commit that changes what users run.** The rules (semver,
one bump per push, `app/__init__.py` only) are in `.claude/skills/versioning/SKILL.md`.

**No AI attribution in commit messages.** No `Co-Authored-By:` trailer, no
"Generated with Claude Code" footer, in commits or PR bodies. (Also stated in root
`CLAUDE.md`.)

---

## Plans

`plans/` holds the maintainer's design notes and working lists and is private. This repository tracks one file in it, `plans/README.md`, and ignores the rest (`/plans/*` in `.gitignore`). Never force-add a file from it. Text from a plan that a reader of the code needs goes into the code comment, the CLAUDE.md section or the commit message that cites the label.

## Don't silently regress a past fix

Refactoring code that holds a past fix can re-introduce the bug, especially a fix that
rests on a cross-file invariant ("only one place sets X, and only under condition Y").

Before changing a method that looks load-bearing:

```
git log --all --oneline -S "<the line I'm changing>" -- <file>
git log --grep="<related keyword>"
```

**The public repository starts at one commit.** A commit hash that a CLAUDE.md file or a code comment cites is in the full development history, which is not published. In a public clone, a cited hash does not resolve: the comment beside it states the reason.

If a fix exists, treat its invariant as load-bearing — the fix commit's message explains
which stray event, race or clobber it guards against. Don't remove or duplicate the
invariant without reading that context.

The `CLAUDE.md` files carry this same kind of rule for `app/xml/` merging,
the SteamCMD download guards, the workshop ACF manifest and `systemd_scope.query`.
Those sections exist because something broke. Read them before touching those paths.
