variable "aws_region" {
  description = "AWS region for the deployment."
  type        = string
  default     = "ap-southeast-2"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "Address range of the VPC. Subnets are carved out of it in network.tf."
  type        = string
  default     = "10.20.0.0/16"
}

variable "app_port" {
  description = "Port the Flask app listens on inside the app tier (matches the Dockerfile)."
  type        = number
  default     = 5000
}

variable "db_port" {
  description = "PostgreSQL port. Only the app tier may reach it."
  type        = number
  default     = 5432
}

variable "db_instance_class" {
  description = "RDS instance size."
  type        = string
  default     = "db.t4g.medium"
}

variable "acm_certificate_arn" {
  description = "ACM certificate for the public HTTPS listener."
  type        = string
  default     = "arn:aws:acm:ap-southeast-2:000000000000:certificate/replace-me"
}
