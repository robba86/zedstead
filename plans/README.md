# plans/

The code, the tests and the CLAUDE.md files in this repository cite labels such as `plans/68 CON-2`, `plans/44 step 6` or `TODO A6`. They refer to the maintainer's design notes and working lists, which are not published: they hold drafts, test logs and private notes that were never written for readers.

A label is still useful. Every mention of the same label belongs to the same piece of work, so `git grep "plans/44"` finds everything built for it. The reason for a rule is written where the rule is: in the code comment or the CLAUDE.md section that cites the label, and in the commit message of the change.

What is planned next is in ROADMAP.md.
