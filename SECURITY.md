# Security

## Reporting a vulnerability

Do not open a public issue for a security problem.

Report it privately on GitHub: open this repository's **Security** tab and choose **Report a vulnerability**. Only the maintainer can read the report. You need a GitHub account.

It helps to include:

- the Zedstead version (`zsm --version`) and how it was installed (the installer, Docker, from source)
- the operating system
- what an attacker needs: no account, a `viewer` or `moderator` account, an `admin` account, or a file the admin installs or imports (a mod, a bundle, a backup, a map)
- the steps to reproduce it

## What counts

Zedstead is a web app that may face the internet and, in system scope, runs commands through sudo. These are treated as vulnerabilities:

- a request without a session that reads or changes anything
- a `viewer` or `moderator` doing something [the roles](https://zedstead.com/docs/web-ui/#accounts-and-roles) give only to a higher role
- something an admin types, or something a mod, bundle, backup or map ships, that reaches a shell, a systemd unit or a path outside the directories Zedstead manages
- a password, token or webhook URL that ends up in a log, a job's output or a file other users of the machine can read

These are known limits, described in [the documentation](https://zedstead.com/docs/), and not vulnerabilities:

- an `admin` is trusted with the server: they edit its files, run RCon commands and start jobs
- a role divides up an admin team; it is not a sandbox for someone you do not trust
- BattlEye RCon is unencrypted UDP; reach a remote server through a VPN or an SSH tunnel
- a bundle contains `config.json` with its credentials, and says so when it is exported

## What to expect

Zedstead is maintained by one person in their spare time, so there is no guaranteed response time. A confirmed problem is fixed on `main`, and the commit describes the fix without the steps to exploit it. Say in your report if you want to be credited.

## Which version gets fixes

There are no release branches. Fixes land on `main`, which is what the installer installs; running the installer again updates an existing install.
