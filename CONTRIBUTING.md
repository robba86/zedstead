Before your first PR is merged you sign the [Contributor License Agreement](CLA.md); CLA Assistant asks for it on the PR. It lets the maintainer license your contribution under the project's [LICENSE](LICENSE) (PolyForm Noncommercial 1.0.0), under another noncommercial license or under an open-source license, never under commercial or proprietary terms. You also consent to the uses that its Moral Rights section lists, such as publication under another name and credit through the git history. Don't include third-party content without an attached permission/license.

AI-assisted contributions are welcome. You are responsible for what you submit:

- run the test suite (`pytest tests/`) and say what you tested, and on what
- point your tool at the CLAUDE.md files — they hold the rules the codebase follows
- be ready to explain the change; PRs nobody can explain get closed

## Documentation

The Documentation section of the pull request body is mandatory. The [pull request template](.github/pull_request_template.md) adds it for you. In it, write:

- what changes for a Zedstead user
- the zedstead.com page that the change touches, as a URL, if you know it
- the new or changed steps, settings or commands, ready to paste

If nothing changes for the user, write "None" and one sentence that says why.

The user documentation lives on [zedstead.com](https://zedstead.com/docs/) and comes from a separate repository. The maintainer moves the text from your pull request into those pages. Do not add documentation Markdown files to this repository.
