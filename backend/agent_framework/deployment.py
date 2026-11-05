"""Fail closed for the explicit single-host HTTPS deployment profile."""
from ipaddress import ip_address

from backend.app.config import ConfigurationError


def require_verified_database(settings):
    db = settings.database
    if db is None or db.sslmode != 'verify-full' or not db.sslrootcert:
        raise ConfigurationError('Framework production requires certificate-verified PostgreSQL TLS.')


def require_live_configuration(configuration):
    if (configuration.profile != 'development'
            or configuration.roles['A02'].kind != 'registered_pdf'
            or configuration.pdf_extraction.mode != 'qwen'
            or configuration.roles['A04_A05'].kind != 'registered_poster'
            or configuration.roles['A06'].kind not in ('reference', 'factory')
            or configuration.roles['A07'].kind != 'factory'
            or configuration.context_preparation is None):
        raise ConfigurationError('Framework production requires real PDF/poster adapters and Neo4j RAG.')


def trusted_proxies(value):
    """Trust exact proxy IPs only; never all callers or a shared public range."""
    if not value:
        return ''
    try:
        addresses = [str(ip_address(item.strip())) for item in value.split(',')]
    except ValueError:
        raise ConfigurationError('TRUSTED_PROXY_IPS must contain exact IP addresses.') from None
    return ','.join(addresses)
