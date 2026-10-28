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

variable "homepage_domain" {
  description = "The fictional clinic's homepage (deploy/sites/komorebi), served by the same Caddy; it embeds the chat widget from var.domain."
  type        = string
  default     = "komorebi.shomasawa.com"
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
  description = "4 GB, so the app (~0.7 GB) and bge-m3's ~1.1 GB of memory-mapped weights both stay in RAM. On a 2 GB t3.small the weights were re-read from EBS on every question (~9 s at gp3's 125 MB/s)."
  type        = string
  default     = "t3.medium"
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
