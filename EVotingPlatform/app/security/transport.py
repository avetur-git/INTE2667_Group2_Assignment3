"""
REQ-14 - Encrypt data in transit
================================

Every connection to the platform should be HTTPS with TLS 1.3, the version the
requirement names. Two places enforce it:

  * Production: the REQ-06 load balancer (infra/load_balancer.tf) accepts only
    HTTPS, with the ELBSecurityPolicy-TLS13-1-2-2021-06 policy.
  * This prototype: run.py serves HTTPS directly when TLS_CERT_FILE and
    TLS_KEY_FILE are set, using tls_context() below, which refuses anything
    older than TLS 1.3. scripts/make_dev_cert.py creates a local certificate.

Over HTTPS every response also carries Strict-Transport-Security (HSTS), which
tells the browser to use HTTPS for this site from then on, even if someone
types or links to http://.
"""
import ssl

from flask import request

HSTS = 'max-age=31536000; includeSubDomains'   # one year


def tls_context(cert_file, key_file):
    """A server TLS context that accepts TLS 1.3 only."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(cert_file, key_file)
    return context


def add_security_headers(response):
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', HSTS)
    return response


def register_transport_security(app):
    app.after_request(add_security_headers)
