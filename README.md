# DWE Cube Adapter

Production-ready deployment adapter for **Cube.js** (semantic layer) + **CubeStore** (pre-aggregation cache) + **Milvus** (vector DB for RAG) + **FastAPI RAG service** + **Streamlit chat UI**.

Managed and hydrated by [DWE-Hub](https://github.com/ponderedw/dwe-hub).

---

## Stack

| Service | Image | Purpose |
|---|---|---|
| `cube_api` | `cubejs/cube:latest` | Cube.js REST / GraphQL / SQL API |
| `cube_refresh_worker` | `cubejs/cube:latest` | Background pre-aggregation refresh |
| `cubestore_router` | `cubejs/cubestore:latest` | CubeStore query router |
| `cubestore_worker_1/2` | `cubejs/cubestore:latest` | CubeStore compute workers |
| `milvus` | `milvusdb/milvus:v2.4.6` | Vector DB for RAG |
| `milvus-etcd` | `quay.io/coreos/etcd:v3.5.5` | Milvus metadata store |
| `milvus-minio` | `minio/minio` | Milvus object storage |
| `fastapi` | `pondered/cube-rag-api:latest` | RAG query API |
| `cube-chat-ui` | `pondered/cube-chat-ui:latest` | Streamlit chat UI |
| `dbt-cube-sync` | built from `Dockerfile.sync` | DBT → Cube → Superset sync (one-shot) |

---

## Quick Start (Local)

```bash
# 1. Copy and fill in environment variables
cp .env.example .env

# 2. Start all services
just up

# 3. Watch logs
just logs

# 4. Run DBT → Cube → Superset sync (optional)
just sync
```

Services will be available at:
- **Cube.js Playground / API**: http://localhost:4000
- **Cube.js SQL API** (Postgres wire): localhost:15432
- **Chat UI**: http://localhost:8501

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
```

Key variables:

| Variable | Description |
|---|---|
| `CUBEJS_DB_TYPE` | Database type (e.g. `postgres`, `redshift`, `snowflake`) |
| `CUBEJS_DB_HOST` | Database host |
| `CUBEJS_DB_PORT` | Database port |
| `CUBEJS_DB_NAME` | Database name |
| `CUBEJS_DB_USER` | Database user |
| `CUBEJS_DB_PASS` | Database password |
| `CUBEJS_API_SECRET` | Secret for JWT token signing (generate with `openssl rand -hex 32`) |
| `DATABASE_URI` | SQLAlchemy URI for dbt-cube-sync |
| `SUPERSET_URL` | Apache Superset URL for sync |
| `SUPERSET_USERNAME` | Superset admin username |
| `SUPERSET_PASSWORD` | Superset admin password |
| `OPENAI_API_KEY` | OpenAI API key (for RAG embeddings) |

---

## Schema (Model)

Cube.js schema files live in `model/cubes/`. These are automatically generated and kept in sync by `dbt-cube-sync`.

To manually trigger a sync:

```bash
just sync
```

This runs:
1. `extract_dbt_metadata.py` — exports dbt manifest
2. `dbt-cube-sync sync-all` — generates/updates `model/cubes/*.yml` and pushes chart changes to Superset

---

## Deploy to AWS

Infrastructure is managed with [Pulumi](https://www.pulumi.com/). It provisions:
- EC2 instance (single-instance, stateful — CubeStore + Milvus need persistent volumes)
- Elastic IP
- Route53 A record (optional)

### Prerequisites

```bash
pip install pulumi pulumi-aws boto3
pulumi login
```

### Deploy

```bash
# Production
just deploy-prod

# Development
just deploy-dev

# Preview changes without applying
just preview-prod
```

### Destroy

```bash
just destroy-dev   # dev environment
just destroy-prod  # prod environment (use with caution)
```

### Configuration

Edit `pulumi/Pulumi.prod.yaml` or `pulumi/Pulumi.dev.yaml`:

```yaml
config:
  dwe-cube:environment: prod
  dwe-cube:git_branch: main
  dwe-cube:dns_name: cube.example.com     # your domain
  dwe-cube:secret_id: my_cube_secrets     # AWS Secrets Manager secret name
  dwe-cube:instance_type: t3.xlarge
  dwe-cube:volume_size: "100"
  dwe-cube:aws_region: us-east-1
  # dwe-cube:vpc_id: vpc-xxxxxxxxx
  # dwe-cube:subnet_id: subnet-xxxxxxxxx
  # dwe-cube:security_group_id: sg-xxxxxxxxx
  # dwe-cube:route53_zone_id: ZXXXXXXXXXX
```

### AWS Secrets Manager

Store your `.env` values as a JSON secret in AWS Secrets Manager:

```json
{
  "CUBEJS_DB_TYPE": "postgres",
  "CUBEJS_DB_HOST": "...",
  "CUBEJS_DB_USER": "...",
  "CUBEJS_DB_PASS": "...",
  "CUBEJS_DB_NAME": "...",
  "CUBEJS_API_SECRET": "...",
  "DATABASE_URI": "postgresql://...",
  "SUPERSET_URL": "https://superset.example.com",
  "SUPERSET_USERNAME": "admin",
  "SUPERSET_PASSWORD": "...",
  "OPENAI_API_KEY": "sk-...",
  "git_deploy_token": "ghp_..."
}
```

The EC2 instance will automatically pull this secret on first boot and write it to `.env`.

---

## DWE-Hub Integration

This repo is an **adapter** managed by [DWE-Hub](https://github.com/ponderedw/dwe-hub).

DWE-Hub can:
1. Hydrate this repo with org-specific config (`.env.example` headers, CI/CD files, Pulumi stack configs)
2. Push changes to a `dwe-hub-<timestamp>` branch for review

To set up: configure the **Cube** adapter in DWE-Hub → Organizations → your org → Deploy tab.

---

## Task Reference

```bash
just              # list all tasks
just up           # start all services (background)
just up-logs      # start all services (foreground with logs)
just down         # stop all services
just restart      # restart all services
just sync         # run DBT → Cube → Superset sync
just logs         # follow all service logs
just logs-service cube_api   # follow a specific service log
just shell        # open shell in cube_api container
just deploy-prod  # deploy production stack via Pulumi
just deploy-dev   # deploy dev stack via Pulumi
just preview-prod # preview production changes
just destroy-dev  # destroy dev infrastructure
just destroy-prod # destroy prod infrastructure
```
