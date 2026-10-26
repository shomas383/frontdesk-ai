variable "region" {
  description = "AWS region (the Settings project's region)."
  type        = string
  default     = "us-east-2"
}

variable "name" {
  description = "Prefix for resource names and the project tag."
  type        = string
  default     = "frontdesk-ai"
}

variable "domain" {
  description = "Public hostname served by Caddy; an A record for it is created in Cloudflare."
  type        = string
  default     = "demo.shomasawa.com"
}

variable "cloudflare_zone_id" {
  description = "Zone ID of shomasawa.com (Cloudflare dashboard → the zone → Overview → API). Passed in so the token needs no Zone:Read."
  type        = string
}

variable "image_repo" {
  description = "Container image the host pulls (public on GHCR)."
  type        = string
  default     = "ghcr.io/shomas383/frontdesk-ai"
}

variable "deploy_tag" {
  description = "Tag the host watches; the deploy workflow moves it to each release."
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "2 GB is enough for app (~0.8 GB, ~1.1 GB peak during ingest) + Postgres + Caddy, with 2 GB swap as headroom."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "Root EBS volume (gp3): OS, Docker images (~3.3 GB per app image), Postgres, swap file."
  type        = number
  default     = 30
}

variable "param_path" {
  description = "Parameter Store path holding the app's secrets and overrides (created by hand, not by Terraform)."
  type        = string
  default     = "/frontdesk-ai/prod"
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}
