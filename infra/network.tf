# The account's default VPC: public subnets in every AZ with an internet gateway already attached, so
# the instance gets internet access without a NAT gateway. IPv4 only (no IPv6 CIDR on this VPC).
data "aws_vpc" "default" {
  default = true
}

# TODO(you): the security group for the instance. compute.tf already uses aws_security_group.web.id,
# so `terraform validate` fails until this exists.
#
# What it must do:
#   - Live in the default VPC (data.aws_vpc.default.id).
#   - Inbound TCP 80 from anywhere: Let's Encrypt's HTTP-01 challenge, and Caddy's redirect to HTTPS.
#   - Inbound TCP 443 from anywhere: the site itself (Caddy can also pass the TLS-ALPN challenge here).
#   - Nothing else inbound. In particular no 22: shell access goes through SSM Session Manager, which
#     works over the agent's *outbound* connection, so no inbound port is needed for it.
#   - Outbound to anywhere. The host and containers call out to ghcr.io (image pulls), Anthropic's API,
#     SSM / Parameter Store, CloudWatch Logs, Let's Encrypt, and the dnf repositories.
#
# How to write it (AWS provider 6):
#   - resource "aws_security_group" "web" { name, description, vpc_id }, with no inline rules.
#   - One resource per rule: aws_vpc_security_group_ingress_rule (security_group_id, cidr_ipv4,
#     ip_protocol = "tcp", from_port, to_port) and aws_vpc_security_group_egress_rule
#     (ip_protocol = "-1" means all protocols; ports are then omitted).
#   - Prefer these separate rule resources over inline ingress/egress blocks: each rule gets its own ID
#     and description, and mixing the two styles makes Terraform fight itself.
#   - Note: Terraform removes AWS's default "allow all outbound" rule from groups it creates, so the
#     egress rule has to be written explicitly.
#
# Check afterwards: `terraform plan` lists the group plus 3 rules; in the console,
# EC2 → Security Groups → <name>-web → Inbound rules shows exactly 80 and 443.
