"""Check README.md's zedstead.com links and docs table against the zedstead-site checkout.

Run from the zsm repo root:  .venv/bin/python .claude/skills/readme-docs/check_readme_docs.py
The site checkout is $ZSM_SITE, or ../zedstead-site beside this repo (as in tests/conftest.py).
Exit 1 on a broken link or a table that does not match DOC_ORDER. A blurb that differs
from the site's index card is printed as a note only.
"""

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SITE = Path(os.environ.get("ZSM_SITE", REPO.parent / "zedstead-site"))
README = REPO / "README.md"


def doc_order() -> list[str]:
    js = (SITE / "src/data/docs.js").read_text(encoding="utf-8")
    # DOC_ORDER is DOC_GROUPS' slugs in order since 2026-11-02; older sites list it.
    m = re.search(r"export const DOC_GROUPS = \[(.*?)\n\]", js, re.S)
    if m:
        return [s for slugs in re.findall(r"slugs: \[([^\]]*)\]", m.group(1)) for s in re.findall(r"'([\w-]+)'", slugs)]
    block = re.search(r"export const DOC_ORDER = \[(.*?)\]", js, re.S).group(1)
    return re.findall(r"'([\w-]+)'", block)


def heading_ids(md: str) -> set[str]:
    """GitHub's anchors, as slugger() in the site's src/data/docs.js makes them."""
    seen: dict[str, int] = {}
    ids = set()
    fenced = False
    for line in md.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"(#{2,6}) (.+)$", line)
        if not m:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
        text = re.sub(r"[`*]", "", text)
        base = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text.lower().strip()))
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.add(f"{base}-{n}" if n else base)
    return ids


def main() -> int:
    if not (SITE / "src/docs").is_dir():
        print(f"No zedstead-site checkout at {SITE}. Set ZSM_SITE.")
        return 1
    readme = README.read_text(encoding="utf-8")
    order = doc_order()
    ids = {slug: heading_ids((SITE / f"src/docs/{slug}.md").read_text(encoding="utf-8")) for slug in order}
    problems = []

    for slug, hash_ in sorted(set(re.findall(r"https://zedstead\.com/docs/([\w-]+)/(?:#([\w-]+))?", readme))):
        if slug not in ids:
            problems.append(f"README links /docs/{slug}/, and the site has no such doc")
        elif hash_ and hash_ not in ids[slug]:
            problems.append(f"README links /docs/{slug}/#{hash_}, and {slug}.md has no such heading")

    table = re.findall(r"^\| \[[^\]]+\]\(https://zedstead\.com/docs/([\w-]+)/\) \| (.+) \|$", readme, re.M)
    rows = [slug for slug, _ in table]
    if rows != order:
        problems.append(f"Docs table rows {rows}\n  must be DOC_ORDER {order}")

    items = json.loads((SITE / "src/locales/en.json").read_text(encoding="utf-8"))["docs"]["items"]
    for slug, blurb in table:
        site = items.get(slug, "").rstrip(".")
        if site and re.sub(r"`", "", blurb) != site:
            print(f"note: {slug} blurb differs from en.json docs.items.{slug}:\n  README: {blurb}\n  site:   {site}")

    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print(f"OK: {len(rows)} docs rows, every zedstead.com/docs link resolves ({SITE})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
