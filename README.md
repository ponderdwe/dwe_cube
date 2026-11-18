# DWE Cube Adapter

Production-ready deployment adapter for **Cube.js** (semantic layer) + **CubeStore** (pre-aggregation cache) + **Milvus** (vector DB for RAG) + **FastAPI RAG service** + **Streamlit chat UI**.

Managed and hydrated by [dwe-core](https://github.com/ponderdwe/dwe-core).

---

## Stack

| Service | Image | Purpose |
|---|---|---|
| `cube_api` | `cubejs/cube:${CUBE_VERSION}` | Cube.js REST / GraphQL / SQL API |
| `cube_refresh_worker` | `cubejs/cube:${CUBE_VERSION}` | Background pre-aggregation refresh |
| `cubestore_router` | `cubejs/cubestore:${CUBE_VERSION}` | CubeStore query router |
| `cubestore_worker_1/2` | `cubejs/cubestore:${CUBE_VERSION}` | CubeStore compute workers |
| `milvus` | `milvusdb/milvus:v2.4.6` | Vector DB for RAG |
| `milvus-etcd` | `quay.io/coreos/etcd:v3.5.5` | Milvus metadata store |
| `milvus-minio` | `minio/minio` | Milvus object storage |
| `fastapi` | `pondered/cube-rag-api:latest` | RAG query API |
| `cube-chat-ui` | `pondered/cube-chat-ui:latest` | Streamlit chat UI |
| `dbt-cube-sync` | built from `Dockerfile.sync` | DBT → Cube schema generation + optional S3 push (one-shot profile) |

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

Copy `.env.example` to `.env` and fill in the required values.

Key variables:

| Variable | Description |
|---|---|
| `CUBEJS_DB_TYPE` | Database type (`postgres`, `redshift`, `snowflake`, …) |
| `CUBEJS_DB_HOST` | Database host |
| `CUBEJS_DB_PORT` | Database port |
| `CUBEJS_DB_NAME` | Database name |
| `CUBEJS_DB_USER` | Database user |
| `CUBEJS_DB_PASS` | Database password |
| `CUBEJS_API_SECRET` | JWT signing secret (`openssl rand -hex 32`) |
| `CUBE_VERSION` | Cube.js + CubeStore image version to use (e.g. `1.1.7`). Do NOT use `latest` in production. |
| `CUBEJS_SCHEMA_S3_BUCKET` | S3 bucket for shared cube schemas. When set, Cube.js reads schemas from S3 instead of local `model/cubes/` |
| `CUBEJS_SCHEMA_S3_PREFIX` | S3 key prefix for cube files (default: `cubes`) |
| `CUBESTORE_REMOTE_DIR` | CubeStore remote storage path. Use `/cube/data` (local EBS, default) or `s3://bucket/cubestore` for HA |
| `CUBEJS_SQL_PASSWORD` | Password to enable the Postgres SQL wire API on port 15432 |
| `DATABASE_URI` | SQLAlchemy URI for dbt-cube-sync |
| `SUPERSET_URL` | Apache Superset URL for sync |
| `SUPERSET_USERNAME` | Superset admin username |
| `SUPERSET_PASSWORD` | Superset admin password |
| `OPENAI_API_KEY` | OpenAI API key (for RAG embeddings) |

---

## Schema (Model)

Cube.js schema files live in `model/cubes/`. These are automatically generated and kept in sync by `dbt-cube-sync`.

Metric definitions in dbt use **MetricFlow v2 inline format** (metrics nested under the
model in `schema.yml`). This is the only format that works end-to-end with dbt-fusion 2.x.
For the full format reference see
[`SEMANTIC_LAYER_FORMATS.md`](https://github.com/ponderedw/dbt-cube-sync/blob/main/SEMANTIC_LAYER_FORMATS.md).

To manually trigger a sync:

```bash
just sync
```

This runs:
1. `extract_dbt_metadata.py` — exports dbt manifest
2. `dbt-cube-sync sync-all` — generates/updates `model/cubes/*.yml` and pushes chart changes to Superset

---

## S3 Schema Sync (Remote-first updates)

When `CUBEJS_SCHEMA_S3_BUCKET` is set, Cube.js reads cube schema files directly from S3 using its built-in `repositoryFactory`. This means you can push schema updates from your laptop or CI without SSH-ing to the server.

### Push schemas from local or CI

```bash
# From local — requires dbt-cube-sync and boto3
dbt-cube-sync sync-all \
  --manifest dbt_metadata/manifest.json \
  --sqlalchemy-uri "$DATABASE_URI" \
  --output model/cubes \
  --s3-bucket my-cube-schema-bucket

# Or using the just recipe:
just sync-s3 my-cube-schema-bucket
```

### How it works

1. `dbt-cube-sync sync-all --s3-bucket` generates cube files locally and uploads them to `s3://bucket/cubes/*.js` + `s3://bucket/cubes/.schema-version`
2. Cube.js polls `schemaVersion()` which reads `.schema-version` from S3
3. When the version changes, Cube.js calls `repositoryFactory` to reload all schema files from S3

No sidecar service, no manual EC2 access.

### Requirements

- `CUBEJS_SCHEMA_S3_BUCKET` set in `.env`
- EC2 IAM role needs `s3:GetObject/ListBucket` on the schema bucket (provisioned by Pulumi when `CUBEJS_SCHEMA_S3_BUCKET` is in Secrets Manager)
- `package.json` in the repo root installs `@aws-sdk/client-s3` on first Cube.js startup

---

## Cloud Deployment

Infrastructure is managed with [Pulumi](https://www.pulumi.com/) and provisioned by dwe-core. Supports **AWS** and **Azure**.

### AWS

Resources provisioned:
- Auto Scaling Group (ASG) with Launch Template
- Application Load Balancer (ALB) for HTTPS on port 443
- Route53 A record: `cube.<domain>` → ALB
- Secrets pulled from **AWS Secrets Manager** at VM boot
- Schema S3 bucket (optional, for remote-push schema updates)

### Azure

Resources provisioned:
- Virtual Machine Scale Set (VMSS) with Automatic upgrade policy
- Application Gateway for HTTPS on port 443
- Standard Load Balancer (NLB) for TCP passthrough on port 15432
- DNS A records:
  - `cube.<domain>` → Application Gateway public IP
  - `cube-sql.<domain>` → NLB public IP
- Secrets pulled from **Azure Key Vault** at VM boot

The SQL wire protocol (`cube-sql.<domain>:15432`) requires `CUBEJS_SQL_PASSWORD` to be set in secrets.

---

## CI/CD

Deployment is triggered automatically on push. Two CI template flavors are available in `ci-templates/`:

- `github.yaml` — GitHub Actions
- `gitlab.yaml` — GitLab CI

**Two-path deploy:**
- `pulumi/**` changed → `pulumi preview` (PR) or `pulumi up` + instance refresh (push)
- App files only → instance refresh only (skips Pulumi)

---

## dwe-core Integration

This repo is a **dwe-core adapter**. Use the `dwe` CLI to create or update deployments:

```bash
# See what copier questions the adapter accepts
dwe adapter-questions dwe_cube

# See required secrets
dwe show-secrets-template dwe_cube --cloud azure

# Create a new deployment repo from this adapter
dwe create-service dwe_cube \
  --git-repo https://github.com/your-org/cube-deploy \
  --envs prod \
  --set git_repo_url=https://github.com/your-org/cube-deploy

# Update an existing deployment to a newer adapter version
dwe update-service dwe_cube ./cube-deploy

# Push secrets to the deployment repo
dwe set-secrets \
  --git-repo https://github.com/your-org/cube-deploy \
  --secrets-file secrets.json \
  --adapter dwe_cube
```

---

## Task Reference

```bash
just              # list all tasks
just up           # start all services (background)
just up-logs      # start all services (foreground with logs)
just down         # stop all services
just restart      # restart all services
just sync         # run DBT → Cube → Superset sync
just sync-s3 my-bucket  # generate schemas locally and push to S3
just logs         # follow all service logs
just logs-service cube_api   # follow a specific service log
just shell        # open shell in cube_api container
```
