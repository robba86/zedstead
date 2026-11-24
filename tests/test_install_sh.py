"""The `install.sh` exit-code contract.

Codes 0-5 are a deliverable in their own right — TODO-2 A8's cloud-config
branches on them — and until now nothing pinned any of them. It is not a
hypothetical: commit `2308693` redefined code 4 ("missing OS deps and
--install-deps was not given" became "missing OS deps and you declined to
install them") and moved its trigger from INSTALL_DEPS to ASSUME_YES, and the
suite stayed green throughout. See plans/28 D1.

**No test here may perform a real install.** Every case below exits before the
first side effect: argument parsing, the --dry-run block, or the confirmation
gate — all of which run before uv, the clone and apt. HOME is repointed at a
tmp dir so even the paths the script *prints* are hermetic. Codes 4 and 5 are
deliberately not provoked: 4 needs the host to genuinely lack packages and 5
needs a root-less box, so the documented-codes test below stands in for them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _run(*args: str, stdin: str | None = None, tmp_home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALL_SH), *args],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_home),
        env={"HOME": str(tmp_home), "PATH": "/usr/bin:/bin", "TERM": "dumb"},
    )


@pytest.fixture()
def tmp_home(tmp_path) -> Path:
    return tmp_path


# --- the exit-code contract ------------------------------------------------


def test_help_exits_zero(tmp_home):
    result = _run("--help", tmp_home=tmp_home)
    assert result.returncode == 0, result.stderr
    assert "zsm installer" in result.stdout


def test_unknown_flag_is_a_usage_error(tmp_home):
    result = _run("--bogus", tmp_home=tmp_home)
    assert result.returncode == 2
    assert "unknown option" in result.stderr


@pytest.mark.parametrize("flag", ["--prefix", "--branch"])
def test_flag_missing_its_argument_is_a_usage_error(flag, tmp_home):
    result = _run(flag, tmp_home=tmp_home)
    assert result.returncode == 2
    assert flag in result.stderr


def test_no_tty_and_no_yes_refuses(tmp_home):
    """The unattended guard: piping the script into sh without --yes must not
    install anything. subprocess gives it a pipe for stdin, which is exactly
    the `curl … | sh` shape."""
    result = _run(tmp_home=tmp_home)
    assert result.returncode == 3
    assert "--yes" in result.stderr


def test_declining_is_also_cancelled(tmp_home):
    """Answering `n` and having no terminal at all are both "cancelled" (3).

    With a pipe on stdin the script never reaches confirm_yn — it takes the
    no-tty branch first — so this pins the *code*, not which branch produced
    it. Provoking the interactive branch would need a pty, and the answer is
    the same either way, which is the point of one code for both.
    """
    result = _run(stdin="n", tmp_home=tmp_home)
    assert result.returncode == 3


def test_dry_run_exits_zero_and_changes_nothing(tmp_home):
    result = _run("--yes", "--dry-run", tmp_home=tmp_home)
    assert result.returncode == 0, result.stderr
    assert "nothing will be changed" in result.stdout
    # --dry-run returns before uv, the clone and apt; the only thing under HOME
    # would be an install dir, and there must not be one.
    assert list(tmp_home.iterdir()) == []



def test_removed_install_deps_is_still_accepted(tmp_home):
    """--install-deps was removed from every published command,
    because --yes installs the OS packages. A cloud-init file written before
    that still passes it, and must not stop at exit 2."""
    result = _run("--yes", "--install-deps", "--dry-run", tmp_home=tmp_home)
    assert result.returncode == 0, result.stderr
    assert "--install-deps" not in _run("--help", tmp_home=tmp_home).stdout

def test_help_documents_every_exit_code(tmp_home):
    """The documented codes and the implemented ones must not drift apart.

    This is what stands in for testing 4 and 5, which cannot be provoked
    safely: if someone adds or renumbers a code, --help has to say so.
    """
    out = _run("--help", tmp_home=tmp_home).stdout
    for code in range(6):
        assert re.search(rf"^\s*{code}\s+\S", out, re.M), f"exit code {code} undocumented"
    assert not re.search(r"^\s*6\s+\S", out, re.M), "a 7th code appeared; update this test"


def test_exit_code_table_matches_the_header_comment():
    """install.sh states the contract twice — the file header (what a reader of
    the raw script sees) and --help. Neither may rot."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    blocks = re.findall(
        r"^#?\s*Exit codes.*?$((?:\n#?\s{2,}\d\s+.*)+)", source, re.M
    )
    assert len(blocks) == 2, f"expected two exit-code tables, found {len(blocks)}"

    def codes(block: str) -> dict[str, str]:
        return {
            m.group(1): m.group(2).strip()
            for m in re.finditer(r"^#?\s+(\d)\s+(.*)$", block, re.M)
        }

    header, help_text = codes(blocks[0]), codes(blocks[1])
    assert header == help_text
    assert set(header) == {"0", "1", "2", "3", "4", "5"}


def test_os_package_prompt_is_gated_on_yes_not_install_deps():
    """`--yes` alone installs OS packages as root; `--install-deps` no longer
    gates that on its own.

    This is the behaviour `2308693` deliberately introduced — "--yes is the only
    thing that suppresses a question" — and it relaxes one of plan 21's own STOP
    conditions ("--install-deps would run without an explicit flag ... it must
    never happen as a side effect"). A safety rule that moved on purpose is
    worth a tripwire: if this gate flips back, or drifts to some third
    variable, whoever does it has to come here and say so. Provoking the real
    path would run apt, which no test may do.
    """
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = source.split("ensure_packages()", 1)[1].split("\nensure_utf8_locale()", 1)[0]
    gate = re.search(r'if \[ "\$(\w+)" -eq 0 \]; then\n\s*warn "Missing OS packages', body)
    assert gate, "the missing-packages prompt gate moved; re-read ensure_packages()"
    assert gate.group(1) == "ASSUME_YES"

    # ...and --help must keep saying so, since that is where a provisioning
    # script author looks before writing `--yes` into cloud-init.
    assert "The only flag that\n                     suppresses a prompt." in source


# --- the published one-liner -----------------------------------------------


# The broken shape, with a real URL in it. Prose that *names* the shape uses an
# ellipsis instead (`sh -c "$(curl …)"`) and is what explains the rule, so it
# must keep being allowed.
_COMMAND_SUBSTITUTION_ONE_LINER = re.compile(r'sh -c "\$\(curl[^)]*http')

_PUBLISHES_A_ONE_LINER = [
    "README.md",
    "install.sh",
    "preflight.sh",
    "uninstall.sh",
    "app/commands/doctor.py",
]


@pytest.mark.parametrize("relative", _PUBLISHES_A_ONE_LINER)
def test_no_command_substitution_one_liner(relative):
    """`sh -c "$(curl …)"` expands to `sh -c ""` when the download fails: an
    empty script runs nothing and exits 0. Measured in 2026 Q3 — curl printed
    `(22) The requested URL returned error: 404` and `$?` was 0, so a
    provisioning script branching on install.sh's exit codes would have read a
    404 as a successful install. Every published form downloads first."""
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    hits = [
        line.strip()
        for line in text.splitlines()
        if _COMMAND_SUBSTITUTION_ONE_LINER.search(line)
    ]
    assert not hits, f"{relative} still publishes the exit-0-on-404 form: {hits}"


# --- git failures: exit 1, never 128, and never a prompt ---------------------
#
# Reaching the clone takes two things: running the script from somewhere that
# is not a checkout, or step 3 takes the local-source branch instead; and
# --check-deps, which skips uv and Python entirely and installs no package.
# Both cases below therefore stop at the clone having done nothing else.


def _run_detached(
    *args: str,
    tmp_home: Path,
    extra_env: dict | None = None,
    extra_path: Path | None = None,
) -> subprocess.CompletedProcess:
    copy = tmp_home / "install.sh"
    copy.write_bytes(INSTALL_SH.read_bytes())
    path = f"{extra_path}:/usr/bin:/bin" if extra_path else "/usr/bin:/bin"
    # Built from scratch, so GIT_TERMINAL_PROMPT is unset here and anything the
    # child sees, install.sh exported itself.
    env = {"HOME": str(tmp_home), "PATH": path, "TERM": "dumb"}
    env.update(extra_env or {})
    return subprocess.run(
        ["sh", str(copy), *args],
        input="",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_home),
        env=env,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_failed_clone_exits_one_not_128(tmp_home):
    """git exits 128 on an authentication failure, a bad branch or a dead
    remote, and `set -e` let that leak out as this script's code. Reproduced
    twice on real boxes from two different causes — bad HTTPS credentials on
    Ubuntu, a missing SSH key on Rocky — both 128, while the contract promises
    1 for "clone failed"."""
    result = _run_detached(
        "--check-deps",
        tmp_home=tmp_home,
        extra_env={"REPO_URL": str(tmp_home / "not-a-repo.git")},
    )
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "Cloning" in result.stderr
    # git's own words, not only ours — a bare "clone failed" is not actionable.
    assert "repository" in result.stderr.lower(), result.stderr


def test_git_never_prompts(tmp_home):
    """--help says --check-deps "does not prompt", and on real boxes it stopped
    at `Username for 'https://github.com':` and at SSH's `Are you sure you want
    to continue connecting (yes/no)?`. Without a terminal those *hang*, which
    is the worst outcome for a cloud-init run.

    A stub git reports what the script exported: provoking the real prompt
    would need a remote that answers 401, and no test here may reach the
    network."""
    bin_dir = tmp_home / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "TERMINAL_PROMPT=[%s] SSH_COMMAND=[%s]\\n" '
        '"$GIT_TERMINAL_PROMPT" "$GIT_SSH_COMMAND" >&2\n'
        "exit 128\n"
    )
    stub.chmod(0o755)

    result = _run_detached("--check-deps", tmp_home=tmp_home, extra_path=bin_dir)

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "TERMINAL_PROMPT=[0]" in result.stderr, result.stderr
    assert "BatchMode=yes" in result.stderr, result.stderr


# --- a token in REPO_URL is never printed -------------------------------------
#
# A private repo is cloned unattended with the token in the URL (plans/40 ran
# that way), and install.sh's output is kept in cloud-init's world-readable log.

_TOKEN = "github_pat_SECRET123"
_TOKEN_URL = f"https://x-access-token:{_TOKEN}@github.com/someone/zsm.git"


def test_dry_run_does_not_print_a_token_in_repo_url(tmp_home):
    result = _run_detached("--yes", "--dry-run", tmp_home=tmp_home, extra_env={"REPO_URL": _TOKEN_URL})

    assert result.returncode == 0, result.stderr
    assert _TOKEN not in result.stdout + result.stderr
    assert "https://***@github.com/someone/zsm.git" in result.stdout


def test_failed_clone_does_not_print_a_token_in_repo_url(tmp_home):
    """Both halves of the failure line: our "Cloning <url>" label and git's own
    words, which name the remote. A stub git stands in for the real one, which
    would need the network to fail this way."""
    bin_dir = tmp_home / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text(
        "#!/bin/sh\n"
        "for a; do case $a in *://*) url=$a ;; esac; done\n"
        'printf "fatal: unable to access \'%s\': The requested URL returned error: 403\\n" "$url" >&2\n'
        "exit 128\n"
    )
    stub.chmod(0o755)

    result = _run_detached(
        "--check-deps", tmp_home=tmp_home, extra_path=bin_dir, extra_env={"REPO_URL": _TOKEN_URL}
    )

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert _TOKEN not in result.stdout + result.stderr
    assert "Cloning https://***@github.com/someone/zsm.git failed" in result.stderr
    assert "unable to access 'https://***@github.com/someone/zsm.git'" in result.stderr


# --- the Python it reports (F7) --------------------------------------------


def test_python_ready_line_reports_an_installed_interpreter():
    """It used to be `uv python list | head -n 1`, which includes versions that
    are merely *downloadable*, newest first. Measured on both VPS boxes:
    "Installed Python 3.11.16" followed by "Python ready:
    cpython-3.15.0rc2-linux-x86_64-gnu <download available>" — an interpreter
    that is not on the machine. Asserted on the flags rather than the output,
    since no test here may run a real install."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    line = next(
        (l for l in source.splitlines() if "Python ready:" in l),
        None,
    )
    assert line, "the Python-ready line is gone"

    listing = next(
        l for l in source.splitlines()
        if "uv python list" in l and not l.lstrip().startswith("#")
    )
    assert "--only-installed" in listing
    # The `3.11` request filter is what makes it the version we asked for and
    # not whichever installed Python happens to sort first.
    assert "3.11" in listing


# --- POSIX shell -----------------------------------------------------------


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_install_sh_is_posix():
    """The remote one-liner runs under /bin/sh, which is dash on Debian family."""
    result = subprocess.run(
        ["dash", "-n", str(INSTALL_SH)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
