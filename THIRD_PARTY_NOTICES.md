# Third-party notices

Zedstead's own code is licensed under the PolyForm Noncommercial License 1.0.0 ([LICENSE](LICENSE)). The files below are the work of other people. That licence does not cover them, and each keeps its own terms.

## Web UI libraries and icons

The built web UI in `app/web/static/` contains JavaScript libraries under the MIT, BSD-2-Clause, BSD-3-Clause and ISC licences, and icons from Heroicons (MIT), Lucide (ISC) and Game-icons (CC BY 3.0, the map editor's markers, the item icons and the building icons, credited by author in `webui/licenses/game-icons.txt`). The build writes every licence text to `app/web/static/third-party-licenses.txt` (see `webui/vite.config.js`).

## Fonts

Inter, JetBrains Mono, Chakra Petch and Teko are licensed under the SIL Open Font License 1.1. The licence and each font's copyright line are in `app/web/static/fonts-OFL.txt`.

## Python packages

The Python packages that Zedstead uses are not in this repository. The installer downloads them from PyPI, and each one keeps its own licence. `pyproject.toml` lists them.

## DayZ, mods and maps

This repository contains no game files, mod files, map files or map images. Zedstead downloads mods and maps from the Steam Workshop through the server owner's own SteamCMD login, and mission files from their authors' own repositories. Their authors' terms apply to them.

The tests in `tests/` download Bohemia's stock mission files from [BohemiaInteractive/DayZ-Central-Economy](https://github.com/BohemiaInteractive/DayZ-Central-Economy) when they run, at a pinned commit. Those files are © BOHEMIA INTERACTIVE a.s. and licensed under the [Arma and DayZ Public License Share Alike (ADPL-SA)](https://www.bohemia.net/community/licenses/arma-and-dayz-public-license-share-alike-adpl-sa). They are not part of this repository.

This app is not affiliated or authorized by Bohemia Interactive. Bohemia Interactive, DAYZ and all associated logos and designs are trademarks or registered trademarks of BOHEMIA INTERACTIVE a.s.
