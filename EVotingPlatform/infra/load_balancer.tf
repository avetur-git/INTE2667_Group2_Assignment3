# The only internet-facing component. HTTPS only (no plain HTTP listener),
# modern TLS, and malformed headers dropped before they reach the app.

#tfsec:ignore:aws-elb-alb-not-public Reviewed exception (REQ-06): voters reach the platform over the internet, so this load balancer has to be public.
resource "aws_lb" "web" {
  name                       = "evoting-${var.environment}"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  enable_deletion_protection = true
}

resource "aws_lb_target_group" "app" {
  name        = "evoting-${var.environment}-app"
  port        = var.app_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.web.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
