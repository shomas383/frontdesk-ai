# All container logs (app JSON lines, Postgres, Caddy) via Docker's awslogs driver, one stream per
# container. Created here, with retention, so nothing ever writes to a group that keeps logs forever.
resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.name}/prod"
  retention_in_days = var.log_retention_days
}
