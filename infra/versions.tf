terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28"
    }
  }

  # State is local (infra/terraform.tfstate, gitignored). Back it up after every apply; see README.md.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.name
      managed-by = "terraform"
    }
  }
}

# Reads the API token from CLOUDFLARE_API_TOKEN (DNS:Edit on the shomasawa.com zone only).
# Provider configuration is never written to state.
provider "cloudflare" {}
