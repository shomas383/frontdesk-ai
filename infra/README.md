# infra/ — the AWS host for demo.shomasawa.com

Terraform for one EC2 instance that runs `deploy/compose.prod.yml` (Caddy → app → Postgres/pgvector), plus the DNS record in Cloudflare. Releases don't go through Terraform: the instance pulls them from GHCR on its own (see [How a release reaches the host](#how-a-release-reaches-the-host)).

## What each resource is

| Terraform resource | What it does | Where to see it in the AWS console |
|---|---|---|
| `data.aws_vpc.default` | The account's default VPC (read, not created). It has public subnets and an internet gateway, so no NAT gateway is needed | VPC → Your VPCs (the "Default VPC: Yes" row) |
| `aws_security_group.web` + its rule resources | Firewall for the instance: inbound 80 and 443 only, no SSH; all outbound | EC2 → Network & Security → Security Groups → `frontdesk-ai-web` |
| `data.aws_ssm_parameter.al2023` | Looks up the latest Amazon Linux 2023 AMI ID from AWS's public parameter | Systems Manager → Parameter Store → Public parameters (or EC2 → AMI Catalog) |
| `aws_instance.web` | The server: t3.medium (4 GB), 30 GB encrypted gp3, IMDSv2 with hop limit 1, standard CPU credits. Its user data (`user_data.sh.tftpl`) sets up swap, Docker and the deploy timer on first boot | EC2 → Instances → `frontdesk-ai-web` (Actions → Monitor → Get system log shows the first boot) |
| `aws_eip.web`, `aws_eip_association.web` | A fixed public IPv4 address, attached to the instance. It survives stop/start and replacing the instance | EC2 → Network & Security → Elastic IPs |
| `aws_iam_role.instance` | The instance's identity. Trusted by EC2 only; path `/`, because this account's SCP reserves `/managed/` | IAM → Roles → `frontdesk-ai-instance` |
| `aws_iam_role_policy_attachment.ssm_core` | AWS's `AmazonSSMManagedInstanceCore` policy: lets the SSM agent register, so Session Manager shells work | IAM → Roles → `frontdesk-ai-instance` → Permissions |
| `aws_iam_role_policy.instance` | Inline policy: write to our log group, read `/frontdesk-ai/prod/*` parameters, decrypt them with the `aws/ssm` key (only via SSM) | same page, the inline policy `frontdesk-ai-instance` |
| `aws_iam_instance_profile.instance` | The wrapper EC2 needs to hand the role to an instance | Not shown separately; EC2 → the instance → Security → IAM Role |
| `data.aws_kms_alias.ssm` | The AWS-managed key that encrypts SecureString parameters (read, not created) | KMS → AWS managed keys → `aws/ssm` |
| `aws_cloudwatch_log_group.app` | `/frontdesk-ai/prod`, 30-day retention. Docker's awslogs driver writes one stream per container (`frontdesk-app-1`, `-db-1`, `-caddy-1`) | CloudWatch → Logs → Log groups |
| `cloudflare_dns_record.demo` | `demo.shomasawa.com` A → the Elastic IP, DNS only (grey cloud) | Cloudflare dashboard → shomasawa.com → DNS → Records |

**Not in Terraform, on purpose:**
- **The two SecureString parameters,** `/frontdesk-ai/prod/ANTHROPIC_API_KEY` and `/frontdesk-ai/prod/POSTGRES_PASSWORD`. They were created by hand in Systems Manager → Parameter Store, so their values never enter Terraform state. Any other parameter under the path (e.g. `DAILY_QUESTION_CAP`) overrides the compose default of the same name on the next deploy.
- **The container image.** It's built by GitHub Actions and stored at `ghcr.io/shomas383/frontdesk-ai`.
- **The AWS budget and the project spend limit.** They're account settings.

## Files

| File | What |
|---|---|
| `versions.tf` | Provider versions and configuration. The AWS region comes from `var.region`; the Cloudflare token comes from `CLOUDFLARE_API_TOKEN` |
| `variables.tf`, `terraform.tfvars.example` | Inputs. Only `cloudflare_zone_id` is required |
| `network.tf`, `compute.tf`, `iam.tf`, `logs.tf`, `dns.tf` | The resources above |
| `outputs.tf` | Instance ID, IP, URL, the Session Manager command, and the user-data size (the EC2 limit is 16 KB; it's ~10 KB now) |
| `user_data.sh.tftpl` | The first-boot script (a Terraform template) |
| `host/pull-deploy.sh`, `host/frontdesk-deploy.{service,timer}` | Embedded into the user data and installed on the host |
| `.terraform.lock.hcl` | Exact provider versions and checksums (Windows + Linux). Committed |

## Applying

Run these in PowerShell, with an `aws login` session and a Cloudflare token limited to Zone → DNS → Edit on shomasawa.com:

```powershell
cd infra
copy terraform.tfvars.example terraform.tfvars   # fill in cloudflare_zone_id
$env:CLOUDFLARE_API_TOKEN = "<token>"
terraform init
terraform plan -out tf.plan
terraform apply tf.plan
terraform output
```

The instance ignores changes to `ami` and `user_data` after creation, so a newer AMI or an edited first-boot script never replaces it, along with its Postgres data and certificates. To really rebuild it, run `terraform apply -replace=aws_instance.web`. The Elastic IP and DNS record stay the same.

## State: local only, back it up

`terraform.tfstate` lives in this folder and is gitignored. It is the only record of which real resources Terraform manages. It holds resource IDs, not secrets.

**After every apply, copy it somewhere safe** (e.g. OneDrive):

```powershell
copy infra\terraform.tfstate "$env:OneDrive\backups\frontdesk-ai\terraform.tfstate.$(Get-Date -Format yyyyMMdd-HHmm)"
```

**If the state is lost,** the resources keep running. Terraform just no longer knows about them, and a plain `apply` would try to create duplicates. Rebuild the state by importing each resource. Find the IDs in the console or with `terraform output` from a backup:

```powershell
terraform import aws_cloudwatch_log_group.app /frontdesk-ai/prod
terraform import aws_iam_role.instance frontdesk-ai-instance
terraform import aws_iam_role_policy.instance frontdesk-ai-instance:frontdesk-ai-instance
terraform import aws_iam_role_policy_attachment.ssm_core frontdesk-ai-instance/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
terraform import aws_iam_instance_profile.instance frontdesk-ai-instance
terraform import aws_security_group.web sg-xxxxxxxx
#   + each rule resource: terraform import aws_vpc_security_group_ingress_rule.<name> sgr-xxxxxxxx
terraform import aws_instance.web i-xxxxxxxx
terraform import aws_eip.web eipalloc-xxxxxxxx
terraform import aws_eip_association.web eipassoc-xxxxxxxx
terraform import cloudflare_dns_record.demo <zone_id>/<record_id>
terraform plan   # should show no changes, or only harmless in-place updates
```

## How a release reaches the host

This account's SCP forbids IAM OIDC providers, so GitHub Actions has no way into AWS. The host pulls instead:

1. A `v*` tag builds the image, pushes `ghcr.io/shomas383/frontdesk-ai:<tag>`, and moves `:prod` to it.
2. On the host, `frontdesk-deploy.timer` runs `/opt/frontdesk/pull-deploy.sh` every 2 minutes. The script:
   - takes a lock, so two runs never overlap
   - pulls `:prod` and compares its digest with `state/deployed-digest`
   - if the digest is new, copies `deploy/` out of that image into `/opt/frontdesk/release/` and runs its `deploy.sh`
3. `deploy.sh`:
   - renders `/opt/frontdesk/.env` from Parameter Store
   - runs `docker compose up -d`; the app ingests the docs, then serves
   - waits until the app is healthy
4. On failure, the digest goes to `state/failed-digest` and is not retried, and the previous digest is redeployed. Every deploy, failure and rollback is a line in `state/history.log` and in `journalctl -u frontdesk-deploy`.

`pull-deploy.sh` itself only changes through a Session Manager shell (or a new instance). Everything else ships inside the image.

## Your parts (TODO)

- **The security group** in `network.tf`.
- **The swap section** in `user_data.sh.tftpl`.

The comments in each file say what's needed and how to check it. Until the security group exists, `terraform validate` reports `Reference to undeclared resource "aws_security_group" "web"`.
