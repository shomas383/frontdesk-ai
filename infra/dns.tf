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

# komorebi.shomasawa.com → the same Elastic IP. Caddy serves the demo clinic's homepage there (a second
# site block) and gets its own certificate; the homepage embeds the chat from demo.shomasawa.com, so the
# widget is genuinely cross-origin, like on a real clinic's site.
resource "cloudflare_dns_record" "homepage" {
  zone_id = var.cloudflare_zone_id
  name    = var.homepage_domain
  type    = "A"
  content = aws_eip.web.public_ip
  ttl     = 300
  proxied = false
  comment = "${var.name} demo clinic homepage (Terraform, infra/dns.tf)"
}
