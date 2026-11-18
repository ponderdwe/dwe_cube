# =============================================================
# DWE Cube — Task Runner
# Requires: just (https://github.com/casey/just)
# =============================================================

# Cube image version — edit .cube-version to change
cube_version := `cat .cube-version | tr -d '[:space:]'`

default:
    @just --list

# ── Local / Development ───────────────────────────────────────

# Start all services (background)
up:
    CUBE_VERSION={{cube_version}} docker compose up -d

# Start all services with logs
up-logs:
    CUBE_VERSION={{cube_version}} docker compose up

# Stop all services
down:
    docker compose down

# Restart all services
restart:
    CUBE_VERSION={{cube_version}} docker compose restart


# ── Pulumi infrastructure ─────────────────────────────────────

deploy-prod:
    cd pulumi && pulumi stack select prod && pulumi up --yes

deploy-dev:
    cd pulumi && pulumi stack select dev && pulumi up --yes

preview-prod:
    cd pulumi && pulumi stack select prod && pulumi preview

destroy-prod:
    cd pulumi && pulumi stack select prod && pulumi destroy

destroy-dev:
    cd pulumi && pulumi stack select dev && pulumi destroy

# ── Utilities ─────────────────────────────────────────────────

logs:
    docker compose logs -f

logs-service service:
    docker compose logs -f {{service}}

shell:
    docker compose exec cube_api sh
