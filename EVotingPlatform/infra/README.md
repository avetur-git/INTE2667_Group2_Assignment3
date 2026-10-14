# infra/ - placeholder for Infrastructure as Code (REQ-06, REQ-16)

Empty on purpose in the base build. When the REQ-06 solution is added, the
Terraform (or OpenTofu) files for firewall rules, security groups and other
security configuration go here, and every change goes through a reviewed pull
request. The same repository history is what the REQ-16 rollback pipeline
reverts to.
