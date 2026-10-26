# Latest Amazon Linux 2023 (x86_64), from AWS's public SSM parameter. It ships the SSM agent and the
# AWS CLI. Only read at creation: lifecycle.ignore_changes keeps a newer AMI from replacing the instance.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  # First-boot script. The host files are embedded base64-encoded so their `$` and quotes reach the
  # instance untouched. Limit: 16 KB (see the user_data_bytes output).
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    pull_deploy_b64    = base64encode(file("${path.module}/host/pull-deploy.sh"))
    deploy_service_b64 = base64encode(file("${path.module}/host/frontdesk-deploy.service"))
    deploy_timer_b64   = base64encode(file("${path.module}/host/frontdesk-deploy.timer"))
    image_repo         = var.image_repo
    deploy_tag         = var.deploy_tag
    param_path         = var.param_path
    log_group          = aws_cloudwatch_log_group.app.name
    region             = var.region
    compose_version    = "v5.6.1"
    compose_sha256     = "47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0"
  })
}

resource "aws_instance" "web" {
  ami                    = data.aws_ssm_parameter.al2023.insecure_value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  vpc_security_group_ids = [aws_security_group.web.id]
  user_data              = local.user_data

  # Unlimited mode bills for sustained CPU above the 20% baseline; standard just slows down instead.
  credit_specification {
    cpu_credits = "standard"
  }

  # IMDSv2 only, and hop limit 1: the host can get the role's credentials, containers (one network hop
  # further) can't.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${var.name}-web"
  }

  # Postgres and the Caddy certificates live on this instance's disk. Never replace it because AWS
  # published a newer AMI or because the first-boot script changed (it only runs once anyway).
  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

# A fixed public IPv4 for the DNS record. Separate from the association so the address survives
# replacing the instance.
resource "aws_eip" "web" {
  domain = "vpc"
  tags = {
    Name = "${var.name}-web"
  }
}

resource "aws_eip_association" "web" {
  instance_id   = aws_instance.web.id
  allocation_id = aws_eip.web.id
}
