# zsm Docker helpers — thin wrappers around `docker compose`.
#
# Docker files split:
#   docker/           Dockerfile, bootstrap.sh, zsm-bootstrap.service, README
#   docker-compose.yml, .dockerignore   stay at repo root (Docker convention)
#   ../zsm-data/     host bind-mount data (server, steam-cache, config, backups, bundles)
#
# Run `make` (or `make help`) to list targets.

COMPOSE ?= docker compose
SVC     ?= dayz

.DEFAULT_GOAL := help
.PHONY: help build up up-fresh down restart refresh logs ps shell login start stop status web web-build web-check dev

help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

build:  ## Build the image
	$(COMPOSE) build

up:  ## Start the container (detached)
	$(COMPOSE) up -d

up-fresh:  ## Recreate the container from current image/config
	$(COMPOSE) up -d --force-recreate

down:  ## Stop and remove the container
	$(COMPOSE) down

restart:  ## Restart the container
	$(COMPOSE) restart

refresh:  ## Full rebuild cycle: down, build, up-fresh, then web
	$(MAKE) down
	${MAKE} web-build
	$(MAKE) build
	$(MAKE) up-fresh
	$(MAKE) web

logs:  ## Follow container logs (systemd journal)
	$(COMPOSE) logs -f

ps:  ## Show container status
	$(COMPOSE) ps

shell:  ## Bash shell in the container as the zsm user
	$(COMPOSE) exec -u zsm $(SVC) bash

login:  ## Cache the Steam login token (interactive, 2FA)
	$(COMPOSE) exec -u zsm $(SVC) zsm steam-login

start:  ## Start the DayZ server (systemd unit)
	$(COMPOSE) exec -u zsm $(SVC) zsm start

stop:  ## Stop the DayZ server
	$(COMPOSE) exec -u zsm $(SVC) zsm stop

status:  ## Show dayz-server.service status
	$(COMPOSE) exec $(SVC) systemctl status dayz-server.service

web:  ## Launch zsm web (browser UI) in the container — foreground, Ctrl+C to stop
	$(COMPOSE) exec -u zsm $(SVC) zsm web

# --- Local dev (no Docker) -------------------------------------------------

dev:  ## Live-reload dev: backend (auto-restart) + Vite HMR on :5173
	./scripts/dev.sh

web-build:  ## Build the web UI (React) into app/web/static — commit the result
	cd webui && npm install && npm run build

web-check:  ## Verify the committed app/web/static bundle matches webui/src
	cd webui && npm ci && npm run build
	@if [ -n "$$(git status --porcelain app/web/static)" ]; then \
		echo "app/web/static/ is stale — commit the rebuilt assets."; \
		git status --porcelain app/web/static; \
		exit 1; \
	fi
	@echo "Committed bundle matches webui/src."
