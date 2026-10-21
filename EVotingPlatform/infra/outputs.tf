output "load_balancer_dns" {
  description = "Public hostname of the voting site."
  value       = aws_lb.web.dns_name
}

output "app_security_group_id" {
  description = "Attach to the app containers so the database accepts their connections."
  value       = aws_security_group.app.id
}

output "app_subnet_ids" {
  description = "Private subnets for the app containers."
  value       = aws_subnet.app[*].id
}

output "app_target_group_arn" {
  description = "Register the app containers here."
  value       = aws_lb_target_group.app.arn
}

output "db_endpoint" {
  description = "PostgreSQL host:port, reachable from the app tier only."
  value       = aws_db_instance.main.endpoint
}
