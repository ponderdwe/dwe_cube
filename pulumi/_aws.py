"""
DWE Cube Infrastructure — Pulumi IaC
Provisions: ALB (HTTP→HTTPS) + ASG + EC2 + Route53 CNAME

Single-instance ASG (stateful — CubeStore + Milvus need persistent volumes).
Rolling update via instance refresh: new instance boots, old drains and terminates.
Run with: pulumi stack select prod && pulumi up --yes
"""

import base64
import json
from pathlib import Path

import boto3  # used only by Pulumi at deploy time (not EC2 bootstrap)
import pulumi
import pulumi_aws as aws
import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Hydration config — written by dwe-core at create-service / update-service time
# ─────────────────────────────────────────────────────────────────────────────
_dwe = yaml.safe_load((Path(__file__).parent / "dwe-hydration.yaml").read_text())
project_name    = _dwe["project_name"]
git_repo_url    = _dwe["git_repo_url"]
adapter_version = _dwe["adapter_version"]

# ─────────────────────────────────────────────────────────────────────────────
# Stack Config
# ─────────────────────────────────────────────────────────────────────────────
config = pulumi.Config()
env          = config.require("environment")
git_branch   = config.require("git_branch")
secret_id    = config.require("secret_id")
instance_type        = config.get("instance_type") or "t3.xlarge"
volume_size          = int(config.get("volume_size") or "100")
aws_region           = config.get("aws_region") or "us-east-1"
app_port             = int(config.get("app_port") or "4000")
# Bump this in CI (e.g. git SHA) to force a new launch template version and trigger instance refresh
startup_code_version = config.get("startup_code_version") or ""

suffix = f"-{env}" if env != "prod" else ""
tags = {
    "Project":     project_name,
    "ManagedBy":   "Pulumi",
    "Environment": env,
    "GitBranch":   git_branch,
}

# ─────────────────────────────────────────────────────────────────────────────
# Load secrets from AWS Secrets Manager
# ─────────────────────────────────────────────────────────────────────────────
def get_secret(sid: str) -> dict:
    client = boto3.client("secretsmanager", region_name=aws_region)
    resp = client.get_secret_value(SecretId=sid)
    return json.loads(resp["SecretString"])

secrets = get_secret(secret_id)

# ── Infrastructure ────────────────────────────────────────────────────────────
vpc_id                = secrets["VPC_ID"]
alb_subnet_ids        = json.loads(secrets["ALB_SUBNET_IDS"])
key_name              = secrets["KEY_NAME"]
ec2_security_group_id = secrets.get("EC2_SECURITY_GROUP_ID", "")
lb_security_group_id  = secrets.get("LB_SECURITY_GROUP_ID", "")
alb_internal          = secrets.get("ALB_INTERNAL", "false").lower() == "true"

# ── Networking / DNS ──────────────────────────────────────────────────────────
route53_zone_id = secrets["ROUTE53_ZONE_ID"]
dns_name        = secrets["DNS_NAME"]
_dns_parts    = dns_name.split(".", 1)
dns_name_sql  = secrets.get("DNS_NAME_SQL") or f"{_dns_parts[0]}-sql"
_dns_fqdn_sql = f"{dns_name_sql}.{_dns_parts[1]}"

existing_target_group_arn = secrets.get("EXISTING_TARGET_GROUP_ARN", "")
existing_alb_dns_name     = secrets.get("EXISTING_ALB_DNS_NAME", "")
use_existing_lb = bool(existing_target_group_arn)
if use_existing_lb:
    if not existing_alb_dns_name:
        raise ValueError("EXISTING_ALB_DNS_NAME is required when EXISTING_TARGET_GROUP_ARN is set")
    acm_certificate_arn = ""
else:
    acm_certificate_arn = secrets["ACM_CERTIFICATE_ARN"]

# ── Git ───────────────────────────────────────────────────────────────────────
git_deploy_token    = secrets["git_deploy_token"]
git_deploy_username = secrets.get("git_deploy_username", "x-token-auth")

# ── Application ───────────────────────────────────────────────────────────────
cubejs_db_type      = secrets["CUBEJS_DB_TYPE"]
cubejs_db_host      = secrets["CUBEJS_DB_HOST"]
cubejs_db_port      = secrets["CUBEJS_DB_PORT"]
cubejs_db_name      = secrets["CUBEJS_DB_NAME"]
cubejs_db_user      = secrets["CUBEJS_DB_USER"]
cubejs_db_pass      = secrets["CUBEJS_DB_PASS"]
cubejs_api_secret   = secrets["CUBEJS_API_SECRET"]
cubejs_sql_password = secrets["CUBEJS_SQL_PASSWORD"]

# ── EC2 DNS (optional) ────────────────────────────────────────────────────────
ec2_dns_zone_id = secrets.get("HOSTED_ZONE_ID_EC2_DNS", "")
ec2_dns_name    = secrets.get("EC2_DNS", "")

# ─────────────────────────────────────────────────────────────────────────────
# IAM — EC2 role (SSM + Bedrock + Secrets Manager)
# ─────────────────────────────────────────────────────────────────────────────
instance_role = aws.iam.Role(
    f"{project_name}-role{suffix}",
    name=f"{project_name}-role{suffix}",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }),
    tags=tags,
)
aws.iam.RolePolicyAttachment(f"{project_name}-ssm{suffix}",     role=instance_role.name, policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
aws.iam.RolePolicyAttachment(f"{project_name}-bedrock{suffix}", role=instance_role.name, policy_arn="arn:aws:iam::aws:policy/AmazonBedrockFullAccess")
aws.iam.RolePolicyAttachment(f"{project_name}-sm{suffix}",      role=instance_role.name, policy_arn="arn:aws:iam::aws:policy/SecretsManagerReadWrite")
if ec2_dns_zone_id:
    # Allow instance to update its own Route53 A record on boot (handles post-refresh IP changes)
    aws.iam.RolePolicy(
        f"{project_name}-r53{suffix}",
        role=instance_role.name,
        policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": ["route53:ChangeResourceRecordSets"],
                           "Resource": f"arn:aws:route53:::hostedzone/{ec2_dns_zone_id}"}],
        }),
    )
if dns_name_sql:
    # Allow instance to self-register the SQL DNS A record in the main hosted zone
    aws.iam.RolePolicy(
        f"{project_name}-r53-sql{suffix}",
        role=instance_role.name,
        policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": ["route53:ChangeResourceRecordSets"],
                           "Resource": f"arn:aws:route53:::hostedzone/{route53_zone_id}"}],
        }),
    )
instance_profile = aws.iam.InstanceProfile(
    f"{project_name}-profile{suffix}", name=f"{project_name}-profile{suffix}",
    role=instance_role.name, tags=tags,
)

# ─────────────────────────────────────────────────────────────────────────────
# Security Groups — empty shells; rules managed separately so the SG itself
# never needs replacing (avoids DependencyViolation on ALB ENIs).
# ─────────────────────────────────────────────────────────────────────────────
vpc_info = aws.ec2.get_vpc(id=vpc_id)
access_cidr = [vpc_info.cidr_block] if alb_internal else ["0.0.0.0/0"]

alb_sg = None
if not use_existing_lb:
    alb_sg = aws.ec2.SecurityGroup(
        f"{project_name}-alb-sg{suffix}",
        name=f"{project_name}-alb-sg{suffix}",
        description="Cube ALB",
        vpc_id=vpc_id,
        tags={**tags, "Name": f"{project_name}-alb-sg{suffix}"},
    )
    aws.ec2.SecurityGroupRule(f"{project_name}-alb-http{suffix}",
        type="ingress", security_group_id=alb_sg.id,
        protocol="tcp", from_port=80, to_port=80, cidr_blocks=access_cidr)
    aws.ec2.SecurityGroupRule(f"{project_name}-alb-https{suffix}",
        type="ingress", security_group_id=alb_sg.id,
        protocol="tcp", from_port=443, to_port=443, cidr_blocks=access_cidr)
    aws.ec2.SecurityGroupRule(f"{project_name}-alb-egress{suffix}",
        type="egress", security_group_id=alb_sg.id,
        protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])

ec2_sg = aws.ec2.SecurityGroup(
    f"{project_name}-ec2-sg{suffix}",
    name=f"{project_name}-ec2-sg{suffix}",
    description="Cube EC2",
    vpc_id=vpc_id,
    tags={**tags, "Name": f"{project_name}-ec2-sg{suffix}"},
)
if use_existing_lb:
    aws.ec2.SecurityGroupRule(f"{project_name}-ec2-app{suffix}",
        type="ingress", security_group_id=ec2_sg.id,
        protocol="tcp", from_port=app_port, to_port=app_port,
        cidr_blocks=[vpc_info.cidr_block])
else:
    aws.ec2.SecurityGroupRule(f"{project_name}-ec2-app{suffix}",
        type="ingress", security_group_id=ec2_sg.id,
        protocol="tcp", from_port=app_port, to_port=app_port,
        source_security_group_id=alb_sg.id)
aws.ec2.SecurityGroupRule(f"{project_name}-ec2-sql{suffix}",
    type="ingress", security_group_id=ec2_sg.id,
    protocol="tcp", from_port=15432, to_port=15432,
    cidr_blocks=[vpc_info.cidr_block])
aws.ec2.SecurityGroupRule(f"{project_name}-ec2-ssh{suffix}",
    type="ingress", security_group_id=ec2_sg.id,
    protocol="tcp", from_port=22, to_port=22, cidr_blocks=access_cidr)
aws.ec2.SecurityGroupRule(f"{project_name}-ec2-egress{suffix}",
    type="egress", security_group_id=ec2_sg.id,
    protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])

# ─────────────────────────────────────────────────────────────────────────────
# AMI
# ─────────────────────────────────────────────────────────────────────────────
ubuntu_ami = aws.ec2.get_ami(
    most_recent=True,
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name",               values=["ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ],
    owners=["099720109477"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Route53 self-registration block — built outside the f-string using plain
# string concat so single-brace JSON chars don't collide with Python f-string
# escaping (double-braces are treated as special).
# ─────────────────────────────────────────────────────────────────────────────
if ec2_dns_name and ec2_dns_zone_id:
    _ip_meta = "local-ipv4" if alb_internal else "public-ipv4"
    _r53_json = (
        '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"'
        + ec2_dns_name
        + '","Type":"A","TTL":60,"ResourceRecords":[{"Value":"__EC2_IP__"}]}}]}'
    )
    _r53_block = f"""
# Self-register EC2 DNS in Route53 — runs on every boot so IP stays current after instance refresh
EC2_IP=$(curl -s http://169.254.169.254/latest/meta-data/{_ip_meta})
R53_CHANGE='{_r53_json}'
R53_CHANGE=$(echo "$R53_CHANGE" | sed "s/__EC2_IP__/$EC2_IP/")
aws route53 change-resource-record-sets \\
    --hosted-zone-id {ec2_dns_zone_id} \\
    --region {aws_region} \\
    --change-batch "$R53_CHANGE" || echo "WARNING: Route53 update failed — check IAM permissions"
"""
else:
    _r53_block = ""

if dns_name_sql:
    _ip_meta_sql = "local-ipv4" if alb_internal else "public-ipv4"
    _r53_json_sql = (
        '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"'
        + _dns_fqdn_sql
        + '","Type":"A","TTL":60,"ResourceRecords":[{"Value":"__CUBE_IP__"}]}}]}'
    )
    _r53_sql_block = f"""
# Self-register Cube SQL DNS A record (direct EC2, for TCP/15432 SQL connections)
CUBE_IP=$(curl -s http://169.254.169.254/latest/meta-data/{_ip_meta_sql})
R53_SQL_CHANGE='{_r53_json_sql}'
R53_SQL_CHANGE=$(echo "$R53_SQL_CHANGE" | sed "s/__CUBE_IP__/$CUBE_IP/")
aws route53 change-resource-record-sets \\
    --hosted-zone-id {route53_zone_id} \\
    --region {aws_region} \\
    --change-batch "$R53_SQL_CHANGE" || echo "WARNING: Route53 SQL DNS update failed"
"""
else:
    _r53_sql_block = ""

# ─────────────────────────────────────────────────────────────────────────────
# User data — install Docker, clone repo, write .env from Secrets Manager
# ─────────────────────────────────────────────────────────────────────────────
user_data_script = f"""#!/bin/bash
set -e
exec > >(tee /var/log/cube-init.log | logger -t cube-init) 2>&1

# startup_code_version={startup_code_version}

apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gnupg-agent software-properties-common git jq unzip

# AWS CLI v2 (EC2 IAM role provides credentials — no keys needed)
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install

# Docker + Compose
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -
add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
apt-get update -y && apt-get install -y docker-ce docker-ce-cli containerd.io
curl -L "https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Fetch all secrets from AWS Secrets Manager
SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id {secret_id} --region {aws_region} --query SecretString --output text)

GIT_USER=$(echo "$SECRET_JSON" | jq -r '.git_deploy_username // "x-token-auth"')
GIT_TOKEN=$(echo "$SECRET_JSON" | jq -r '.git_deploy_token')

# Clone repo with credentials injected into URL
REPO_URL="{git_repo_url}"
REPO_PATH=$(echo "$REPO_URL" | sed 's,https://,,')
git clone "https://$GIT_USER:$GIT_TOKEN@$REPO_PATH" /home/ubuntu/cube
git -C /home/ubuntu/cube checkout {git_branch}
git -C /home/ubuntu/cube rev-parse HEAD > /home/ubuntu/cube/.schema-version

# Write .env from all secret key=value pairs
echo "$SECRET_JSON" | jq -r 'to_entries[] | .key + "=" + (.value | tostring)' > /home/ubuntu/cube/.env

cd /home/ubuntu/cube && docker-compose up -d
{_r53_block}{_r53_sql_block}"""
user_data = base64.b64encode(user_data_script.encode()).decode()

# ─────────────────────────────────────────────────────────────────────────────
# Launch Template
# ─────────────────────────────────────────────────────────────────────────────
lt = aws.ec2.LaunchTemplate(
    f"{project_name}-lt{suffix}",
    name_prefix=f"{project_name}{suffix}-",
    image_id=ubuntu_ami.id,
    instance_type=instance_type,
    key_name=key_name,
    vpc_security_group_ids=[ec2_sg.id] + ([ec2_security_group_id] if ec2_security_group_id else []),
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(name=instance_profile.name),
    block_device_mappings=[aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
        device_name="/dev/sda1",
        ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
            volume_size=volume_size, volume_type="gp2",
            encrypted=True, delete_on_termination=True,
        ),
    )],
    user_data=user_data,
    tag_specifications=[aws.ec2.LaunchTemplateTagSpecificationArgs(
        resource_type="instance",
        tags={**tags, "Name": f"cube{suffix}"},
    )],
    tags=tags,
)

# ─────────────────────────────────────────────────────────────────────────────
# ALB
# ─────────────────────────────────────────────────────────────────────────────
alb = None
tg  = None
if not use_existing_lb:
    alb_sg_ids = [alb_sg.id] + ([lb_security_group_id] if lb_security_group_id else [])
    alb = aws.lb.LoadBalancer(
        f"{project_name}-alb{suffix}",
        name=f"{project_name}-alb{suffix}",
        internal=alb_internal,
        load_balancer_type="application",
        security_groups=alb_sg_ids,
        subnets=alb_subnet_ids,
        idle_timeout=600,
        tags={**tags, "Name": f"{project_name}-alb{suffix}"},
    )

    tg = aws.lb.TargetGroup(
        f"{project_name}-tg{suffix}",
        name=f"{project_name}-tg{suffix}",
        port=app_port,
        protocol="HTTP",
        vpc_id=vpc_id,
        deregistration_delay=30,
        health_check=aws.lb.TargetGroupHealthCheckArgs(
            enabled=True,
            path="/readyz",
            port=str(app_port),
            protocol="HTTP",
            healthy_threshold=2,
            interval=30,
            timeout=10,
            unhealthy_threshold=3,
            matcher="200",
        ),
        tags={**tags, "Name": f"{project_name}-tg{suffix}"},
    )

    aws.lb.Listener(
        f"{project_name}-http{suffix}",
        load_balancer_arn=alb.arn,
        port=80,
        protocol="HTTP",
        default_actions=[aws.lb.ListenerDefaultActionArgs(
            type="redirect",
            redirect=aws.lb.ListenerDefaultActionRedirectArgs(port="443", protocol="HTTPS", status_code="HTTP_301"),
        )],
    )

    aws.lb.Listener(
        f"{project_name}-https{suffix}",
        load_balancer_arn=alb.arn,
        port=443,
        protocol="HTTPS",
        ssl_policy="ELBSecurityPolicy-TLS13-1-2-2021-06",
        certificate_arn=acm_certificate_arn,
        default_actions=[aws.lb.ListenerDefaultActionArgs(type="forward", target_group_arn=tg.arn)],
    )

# ─────────────────────────────────────────────────────────────────────────────
# Auto Scaling Group (min=max=1 — stateful, single instance)
# ─────────────────────────────────────────────────────────────────────────────
asg = aws.autoscaling.Group(
    f"{project_name}-asg{suffix}",
    name=f"{project_name}-asg{suffix}",
    min_size=1, max_size=2, desired_capacity=1,
    vpc_zone_identifiers=alb_subnet_ids,
    target_group_arns=[existing_target_group_arn if use_existing_lb else tg.arn],
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(id=lt.id, version="$Latest"),
    health_check_type="ELB",
    health_check_grace_period=600,
    instance_refresh=aws.autoscaling.GroupInstanceRefreshArgs(
        strategy="Rolling",
        preferences=aws.autoscaling.GroupInstanceRefreshPreferencesArgs(
            min_healthy_percentage=0,
            instance_warmup=300,
        ),
    ),
    tags=[aws.autoscaling.GroupTagArgs(key=k, value=v, propagate_at_launch=True) for k, v in {**tags, "Name": f"cube{suffix}"}.items()],
)

# ─────────────────────────────────────────────────────────────────────────────
# Route53 — CNAME → ALB
# ─────────────────────────────────────────────────────────────────────────────
aws.route53.Record(
    f"{project_name}-dns{suffix}",
    zone_id=route53_zone_id,
    name=dns_name,
    type="CNAME",
    ttl=30,
    records=[existing_alb_dns_name if use_existing_lb else alb.dns_name],
)

# ─────────────────────────────────────────────────────────────────────────────
# Route53 — EC2 A record is managed by the instance itself (see user_data above).
# Self-registration via `aws route53 change-resource-record-sets` runs on every
# boot, so the record stays accurate after instance refresh without needing
# another `pulumi up`. The EC2 IAM role has ChangeResourceRecordSets permission
# for this hosted zone (see r53 RolePolicy above).
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# KG Phase 2 — register deployed services with the Deploy Management API.
# No-op when KG_API_HOST / KG_API_TOKEN absent or kg_mappings not in dwe-hydration.yaml.
# dwe-core writes kg_mappings into dwe-hydration.yaml at create-service time
# (_inject_kg_registration). This block is static Python; nothing is injected here.
# ─────────────────────────────────────────────────────────────────────────────
_kg_host     = secrets.get("KG_API_HOST", "")
_kg_token    = secrets.get("KG_API_TOKEN", "")
_kg_mappings = _dwe.get("kg_mappings")

if _kg_host and _kg_token and _kg_mappings:
    import httpx as _httpx
    import warnings as _warnings

    _adapter_name  = _kg_mappings["adapter_name"]
    _kg_props_keys = _kg_mappings.get("kg_adapter_properties", {})  # {prop: SECRET_KEY}
    _kg_outputs    = _kg_mappings.get("kg_pulumi_outputs", {})       # {alias: export_name}
    _kg_services   = _kg_mappings.get("services", [])

    # Map export names referenced in kg_pulumi_outputs to their pulumi.Output objects
    _pulumi_export_map = {
        "alb_dns":  alb.dns_name if alb else pulumi.Output.from_input(""),
        "url":      pulumi.Output.from_input(f"https://{dns_name}"),
        "asg_name": asg.name,
    }
    _out_names = list(_kg_outputs.keys())
    _out_vals  = [_pulumi_export_map[_kg_outputs[n]] for n in _out_names]

    def _phase2_hydrate(*resolved):
        props = dict(zip(_out_names, resolved))
        for prop, secret_key in _kg_props_keys.items():
            props[prop] = secrets.get(secret_key, "")

        _headers = {"Authorization": f"Bearer {_kg_token}"}
        _base    = _kg_host.rstrip("/")
        try:
            _httpx.patch(
                f"{_base}/adapters/{_adapter_name}/{env}",
                json={"properties": props},
                headers=_headers,
                timeout=10,
            )
        except Exception as _exc:
            _warnings.warn(f"[dwe-kg] PATCH /adapters failed: {_exc}")

        # Only register services whose trigger_secret is present in secrets
        _svc_payloads = []
        for _svc in _kg_services:
            _trigger = _svc.get("trigger_secret", "")
            if _trigger and not secrets.get(_trigger):
                continue
            _svc_props = {p: secrets.get(sk, "") for p, sk in _svc.get("properties", {}).items()}
            _svc_payloads.append({"name": _svc["name"], "properties": _svc_props})

        if _svc_payloads:
            try:
                _httpx.post(
                    f"{_base}/adapters/{_adapter_name}/{env}/services",
                    json={"services": _svc_payloads},
                    headers=_headers,
                    timeout=10,
                )
            except Exception as _exc:
                _warnings.warn(f"[dwe-kg] POST /services failed: {_exc}")

    pulumi.Output.all(*_out_vals).apply(_phase2_hydrate)

# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────
if alb:
    pulumi.export("alb_dns", alb.dns_name)
pulumi.export("url",          f"https://{dns_name}")
pulumi.export("sql_endpoint", f"{_dns_fqdn_sql}:15432")
pulumi.export("asg_name",     asg.name)
pulumi.export("environment",  env)
