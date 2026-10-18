"""Tests for SSRF protection and URL metadata extractor.

Ensures:
- HTTPS requirement
- Prohibited loopback, private, link-local and reserved IP rejection
- Domain allowlist enforcement
- Disabled by default fallback to demo catalogue
- No external public network calls needed
"""
import pytest
from app.services.metadata_extractor import (
    DisabledMetadataExtractor,
    ExtractedProductMetadata,
    SafeUrlMetadataExtractor,
    get_metadata_extractor,
    is_ip_allowed,
    validate_url_for_ssrf,
)


def test_ip_allowed_filters_private_and_loopback():
    assert is_ip_allowed("8.8.8.8") is True
    assert is_ip_allowed("1.1.1.1") is True
    assert is_ip_allowed("127.0.0.1") is False
    assert is_ip_allowed("10.0.0.1") is False
    assert is_ip_allowed("192.168.1.1") is False
    assert is_ip_allowed("172.16.0.1") is False
    assert is_ip_allowed("169.254.169.254") is False
    assert is_ip_allowed("::1") is False
    assert is_ip_allowed("fe80::1") is False
    assert is_ip_allowed("invalid-ip") is False


def test_validate_url_requires_https():
    with pytest.raises(ValueError, match="only HTTPS"):
        validate_url_for_ssrf("http://amazon.in/dp/B08N5WRWNW", ["amazon.in"])

    with pytest.raises(ValueError, match="only HTTPS"):
        validate_url_for_ssrf("ftp://amazon.in/file", ["amazon.in"])


def test_validate_url_rejects_localhost_and_loopback():
    with pytest.raises(ValueError, match="loopback and localhost"):
        validate_url_for_ssrf("https://localhost/test", ["localhost"])

    with pytest.raises(ValueError, match="loopback and localhost"):
        validate_url_for_ssrf("https://127.0.0.1/test", ["127.0.0.1"])


def test_validate_url_rejects_unlisted_domains():
    with pytest.raises(ValueError, match="not in the configured allowlist"):
        validate_url_for_ssrf("https://evil-site.com/product", ["amazon.in", "flipkart.com"])


def test_validate_url_rejects_private_dns_resolution(monkeypatch):
    import socket
    # Mock socket.getaddrinfo to simulate domain resolving to a private IP
    def mock_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    with pytest.raises(ValueError, match="resolves to a restricted/private IP"):
        validate_url_for_ssrf("https://amazon.in/dp/test", ["amazon.in"])


def test_validate_url_accepts_valid_public_domain(monkeypatch):
    import socket
    def mock_getaddrinfo(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    url = "https://amazon.in/dp/B08N5WRWNW"
    assert validate_url_for_ssrf(url, ["amazon.in"]) == url


def test_disabled_metadata_extractor_returns_none():
    import asyncio
    extractor = DisabledMetadataExtractor()
    result = asyncio.run(extractor.extract("https://amazon.in/dp/test"))
    assert result is None


def test_get_metadata_extractor_default_disabled():
    extractor = get_metadata_extractor()
    assert isinstance(extractor, DisabledMetadataExtractor)
