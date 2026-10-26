output "instance_id" {
  value = aws_instance.web.id
}

output "public_ip" {
  value = aws_eip.web.public_ip
}

output "url" {
  value = "https://${var.domain}"
}

output "session_command" {
  description = "Open a root-capable shell on the instance (no SSH)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.web.id}"
}

output "user_data_bytes" {
  description = "Size of the first-boot script; EC2's limit is 16384."
  value       = length(local.user_data)
}
