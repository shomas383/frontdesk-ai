# The account's default VPC: public subnets in every AZ with an internet gateway already attached, so
# the instance gets internet access without a NAT gateway. IPv4 only (no IPv6 CIDR on this VPC).
data "aws_vpc" "default" {
  default = true
}

# The firewall around the server. A security group is "deny everything" by
# default; each rule below opens one specific path.
resource "aws_security_group" "web" {
  name        = "${var.name}-web"
  description = "Public HTTP/HTTPS to Caddy. No SSH: shell access is via SSM Session Manager."
  vpc_id      = data.aws_vpc.default.id # the account's default network in us-east-2

  tags = {
    Name = "${var.name}-web" # the name shown in the console's list view
  }
}

# Port 80 (HTTP) from anywhere. Needed for two reasons:
# 1) Let's Encrypt checks we own the domain by fetching a file over port 80 (HTTP-01).
# 2) Caddy redirects plain http:// visitors to https://.
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.web.id
  description       = "HTTP: ACME challenge + redirect to HTTPS"
  cidr_ipv4         = "0.0.0.0/0" # 0.0.0.0/0 = every IPv4 address
  ip_protocol       = "tcp"
  from_port         = 80 # from/to is a port RANGE; same number = one port
  to_port           = 80
}

# Port 443 (HTTPS) from anywhere: the actual website traffic.
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.web.id
  description       = "HTTPS: the demo site"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Everything outbound. The console adds this automatically, but Terraform
# REMOVES that default, so without this rule the server couldn't reach
# GHCR (images), Anthropic (LLM), SSM (Session Manager), CloudWatch (logs),
# Let's Encrypt (certs) or the package mirrors.
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.web.id
  description       = "All outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1" # -1 = all protocols; ports must be omitted
}

# No port 22 on purpose: Session Manager works by the SSM agent on the server
# connecting OUT to AWS, so no inbound port is needed for shell access.
