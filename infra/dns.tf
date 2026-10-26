# demo.shomasawa.com → the Elastic IP. DNS only (grey cloud): visitors connect straight to Caddy, which
# gets its own Let's Encrypt certificate and sees the real client IP for rate limiting.
resource "cloudflare_dns_record" "demo" {
  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "A"
  content = aws_eip.web.public_ip
  ttl     = 300
  proxied = false
  comment = "${var.name} demo (Terraform, infra/dns.tf)"
}
