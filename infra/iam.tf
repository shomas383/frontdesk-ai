# The instance's identity. Containers can't use it (IMDS hop limit 1 in compute.tf); the host's Docker
# daemon (awslogs), the SSM agent and the AWS CLI in deploy.sh can. Default path "/": this account's
# SCP reserves /managed/.

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name}-instance"
  path               = "/"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Session Manager shells and Run Command (the AWS-managed policy the SSM agent needs).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# The AWS-managed key that encrypts SecureString parameters. It exists once a SecureString has been
# created in this region, which is already the case.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

locals {
  param_arn = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.param_path}"
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "WriteContainerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }

  statement {
    sid       = "ReadAppParameters"
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = [local.param_arn, "${local.param_arn}/*"]
  }

  statement {
    sid       = "DecryptSecureStrings"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.name}-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance"
  path = "/"
  role = aws_iam_role.instance.name
}
