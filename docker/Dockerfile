# DayZ dedicated server image — runs *systemd as PID 1* so the container behaves
# exactly like a native Ubuntu server install: `zsm` installs and manages the
# real `dayz-server.service` and per-task systemd .timer units, no special-case
# Docker code path. zsm itself stays Docker-agnostic — the container glue lives
# in the bootstrap unit + script, not in the app.
#
# Build context is the repo root:
#   docker build -t zedstead-dayz -f docker/Dockerfile .
#
# Running systemd in a container needs cgroup access — see docker-compose.yml
# (cgroup: host + /sys/fs/cgroup mount + tmpfs /run). Stop the container with
# SIGRTMIN+3 so systemd shuts the server down cleanly.
FROM ubuntu:24.04

ARG UID=1000
ARG GID=1000
ARG PYTHON_VERSION=3.12

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    HOME=/home/zsm \
    PATH=/home/zsm/.local/bin:/usr/local/bin:$PATH \
    # Tell systemd it's in a container (skips host-only units) and tell zsm too.
    container=docker \
    ZSM_DOCKER=1 \
    # This image runs systemd as PID 1 with NOPASSWD sudo, so services are
    # system-scope; user-scope units don't work here at all. Declaring it in the
    # env (rather than config.json) keeps the guarantee independent of whether
    # `zsm container-init` ran — ZSM_WRITE_CONFIG=0 skips that, and without
    # this the scope heuristics fall through to "user" on a box whose unit isn't
    # installed yet, routing Start/Stop to a systemctl --user that cannot work.
    ZSM_SERVICE_SCOPE=system \
    # Tell Rich the terminal is 24-bit. Without COLORTERM, Docker's bare
    # TERM makes them degrade the theme's truecolor hex values to 256/16-color.
    TERM=xterm-256color \
    COLORTERM=truecolor

# --- system + runtime deps ---------------------------------------------------
# systemd + systemd-sysv make systemd the init system. `dbus` is NOT optional
# here: the D-Bus system bus is only a *Recommends* of systemd, and this install
# is --no-install-recommends, so without listing it explicitly the container has
# no /run/dbus/system_bus_socket. Root systemctl still works (it reaches the
# manager over /run/systemd/private), but every *non-root* systemctl call fails
# — which is all of zsm's state reads (`show ActiveState`, `is-enabled`,
# MainPID). They came back empty and the app concluded the server was stopped
# while it was running: Stop short-circuited with "Service already stopped" and
# the status grid stayed grey. sudo lets the zsm user install/manage units
# (passwordless, configured below) exactly like a native
# admin. SteamCMD is 32-bit, so enable i386 and add the documented minimum libs
# plus libcurl. The DayZ Linux server binary is 64-bit and may link more of the
# Steam runtime: if `DayZServer` dies with a missing-lib loader error in your VM,
# run `ldd ./DayZServer` there and add the reported packages here. This list
# can't be verified in CI (server files aren't present — project rule).
#
# The non-container half of this list is mirrored in app/data/os-packages.txt,
# which install.sh and `zsm doctor` read;
# tests/test_os_packages.py fails if the two drift. Container-only extras
# (systemd, systemd-sysv, dbus, sudo, ncurses-term, openssh-client) are expected
# here and allowlisted in that test — do not copy them into the VPS list.
# openssh-client is ssh-keyscan, which reads an SFTP offsite host's key before
# the admin confirms it; a VPS has it already, since its sshd depends on it.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        systemd systemd-sysv \
        dbus \
        sudo \
        ca-certificates \
        curl \
        locales \
        ncurses-term \
        openssh-client \
        tzdata \
        tar \
        lib32gcc-s1 \
        libstdc++6:i386 \
        libcurl4 \
    && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# --- trim systemd for containers --------------------------------------------
# Mask units that make no sense in a container (hardware, udev, getty, ttys).
# Left running they just spew failures into the journal and slow boot. `mask`
# works offline at build time (it only symlinks into /etc/systemd/system).
RUN systemctl set-default multi-user.target \
    && systemctl mask \
        dev-hugepages.mount \
        sys-fs-fuse-connections.mount \
        sys-kernel-config.mount \
        sys-kernel-debug.mount \
        sys-kernel-tracing.mount \
        systemd-udevd.service \
        systemd-udevd-control.socket \
        systemd-udevd-kernel.socket \
        systemd-udev-trigger.service \
        systemd-modules-load.service \
        e2scrub_reap.service \
        getty.target \
        console-getty.service

# --- non-root user -----------------------------------------------------------
# ubuntu:24.04 ships a stock `ubuntu` user at UID 1000; drop it so zsm can take
# 1000 (matches the host uid for writable bind mounts). Passwordless sudo lets
# the zsm user run `service install` / `install-timers` (systemctl + writes to
# /etc/systemd/system) with no tty — the container equivalent of a sudo admin.
RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd -g "${GID}" zsm \
    && useradd -m -u "${UID}" -g "${GID}" -s /bin/bash zsm \
    && echo 'zsm ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/zsm \
    && chmod 0440 /etc/sudoers.d/zsm

# --- uv (single static binary) ----------------------------------------------
# Runs as root with HOME=/home/zsm set, so it may create root-owned dirs under
# zsm's home; chown back so the unprivileged user can write at runtime.
RUN curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh \
    && chown -R zsm:zsm /home/zsm

# --- install zsm (as the zsm user) ----------------------------------------
# Pin a Python for uv, then install the package as a uv tool (mirrors
# install.sh). Done after the slow apt/uv layers so app edits don't rebuild it.
USER zsm
WORKDIR /home/zsm
RUN uv python install "${PYTHON_VERSION}"
COPY --chown=zsm:zsm . /home/zsm/src
RUN uv tool install --python "${PYTHON_VERSION}" /home/zsm/src \
    && rm -rf /home/zsm/.cache/uv

# --- SteamCMD ----------------------------------------------------------------
# Extracted here; it self-updates on first run. The tar build downloads game +
# workshop content under $HOME/Steam regardless of +force_install_dir, which is
# also zsm's default workshop dir — so that path is a persisted volume.
# The curl flags are app/shared/steam.py's STEAMCMD_CURL_ARGV (plans/68 NET-12):
# `-q` first so no .curlrc is read, `-f` so an HTTP error fails the step, https
# only, TLS 1.2 or later, and a deadline. Downloaded to a file, then extracted:
# /bin/sh has no pipefail, so a pipe would report only tar's exit status.
RUN mkdir -p /home/zsm/steamcmd /home/zsm/server /home/zsm/Steam \
    && curl -q -fsSL --proto =https --proto-redir =https --tlsv1.2 \
        --connect-timeout 30 --max-time 600 \
        -o /home/zsm/steamcmd/steamcmd_linux.tar.gz \
        https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
    && tar -xzf /home/zsm/steamcmd/steamcmd_linux.tar.gz -C /home/zsm/steamcmd \
    && rm /home/zsm/steamcmd/steamcmd_linux.tar.gz

# --- container bootstrap unit (runs as root at boot) ------------------------
USER root
COPY docker/bootstrap.sh /usr/local/bin/zsm-bootstrap
COPY docker/zsm-bootstrap.service /etc/systemd/system/zsm-bootstrap.service
RUN chmod +x /usr/local/bin/zsm-bootstrap \
    && systemctl enable zsm-bootstrap.service

# Documentation only: docker-compose.yml runs this image with
# `network_mode: host`, so nothing is published and these ports open directly
# on every host interface, behind the host's own firewall.
# game / voice / extra (2302-2306), Steam query (27016). BattlEye RCON is 2305,
# already inside the range above, and listens on every interface unless
# beserver_x64.cfg sets `RConIP 127.0.0.1`. The web panel (`zsm web`, opt-in,
# which also serves the Visual Map Editor) is 8433, HTTPS with a self-signed
# certificate unless `web_bind_host` is set.
EXPOSE 2302-2306/udp 27016/udp 8433/tcp

# systemd shuts down cleanly on SIGRTMIN+3 (Docker's default SIGTERM would be
# treated as a reboot request). docker-compose.yml sets the matching stop_signal.
STOPSIGNAL SIGRTMIN+3

ENTRYPOINT ["/sbin/init"]
