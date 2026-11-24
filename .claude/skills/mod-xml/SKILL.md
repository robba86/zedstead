---
name: mod-xml
description: Rules for how mod XML fragments reach a DayZ mission — the CE-linked custom/ copies registered in cfgeconomycore.xml, the direct START/END MOD merges, manual XML files, and the exclusion lists. Use when working on mod XML merging, cfgeconomycore, custom/ generated files, manual XML files, the Mod XML page, or the xml_report — from anywhere in the repo, especially outside app/xml/.
---

# Mod XML — where the rules live

The full ruleset is `app/xml/CLAUDE.md`. It loads automatically when you
work under `app/xml/`. **Read it now if you are not there** — this subject spans four
directories and the rules are unforgiving:

| You are editing | Read |
|---|---|
| `app/xml/*` | `app/xml/CLAUDE.md` (auto-loaded) |
| `app/web/routers/modxml.py`, `app/web/editors/ce_tables.py` | `app/xml/CLAUDE.md` **and** `app/web/CLAUDE.md` |
| `webui/src/pages/ModXmlPage.jsx`, `ManualXmlFiles.jsx` | `app/xml/CLAUDE.md` **and** `webui/CLAUDE.md` |
| `app/commands/run.py`, `app/ops/pipeline.py`, `app/commands/mods.py` | `app/xml/CLAUDE.md` |

The three things that break silently if you skip it: `custom/` is wiped and rebuilt on
every merge (never treat a file there as user data), merges must never key on a mod
file's root tag, and per-mod paths must never restore from `.backup`.
