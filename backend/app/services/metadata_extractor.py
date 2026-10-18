"""SSRF-safe product URL metadata extractor interface and implementations.

All remote extraction is disabled by default and falls back gracefully to the demo catalogue.
When enabled, strict SSRF defenses protect against intranet scanning and data exfiltration.
"""
from abc import ABC, abstractmethod
from html.parser import HTMLParser
import ipaddress
import re
import socket
from typing import List, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from app.config import settings


class ExtractedProductMetadata(BaseModel):
    product_name: Optional[str] = None
    brand: Optional[str] = None
    image_url: Optional[str] = None
    description: Optional[str] = None


class MetadataExtractorProvider(ABC):
    @abstractmethod
    async def extract(self, url: str) -> Optional[ExtractedProductMetadata]:
        """Extract product metadata from the provided URL."""
        pass


class DisabledMetadataExtractor(MetadataExtractorProvider):
    async def extract(self, url: str) -> Optional[ExtractedProductMetadata]:
        # Remote extraction is disabled by default; returns None to fall back to demo catalogue.
        return None


class SimpleMetaHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta_tags = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "meta":
            attr_dict = {k.lower(): v for k, v in attrs if v is not None}
            key = attr_dict.get("property") or attr_dict.get("name")
            content = attr_dict.get("content")
            if key and content:
                self.meta_tags[key.lower()] = content


def is_ip_allowed(ip_str: str) -> bool:
    """Validate that the IP is a valid public IP and not private, loopback, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
        # Specific check for IPv4 mapped in IPv6
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return is_ip_allowed(str(ip.ipv4_mapped))
        return True
    except ValueError:
        return False


def validate_url_for_ssrf(url: str, allowed_domains: List[str]) -> str:
    """Validate scheme, allowed domains, and resolve host to ensure non-private IP."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("SSRF protection: only HTTPS URLs are permitted")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("SSRF protection: missing hostname in URL")

    # Check localhost / direct loopback name
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("SSRF protection: loopback and localhost destinations are prohibited")

    # Check allowlist
    domain_allowed = any(
        hostname.lower() == d.lower() or hostname.lower().endswith("." + d.lower())
        for d in allowed_domains
    )
    if not domain_allowed:
        raise ValueError(f"SSRF protection: domain '{hostname}' is not in the configured allowlist")

    # DNS Resolution check
    try:
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if not is_ip_allowed(ip_str):
                raise ValueError(f"SSRF protection: host '{hostname}' resolves to a restricted/private IP ({ip_str})")
    except socket.gaierror as e:
        raise ValueError(f"SSRF protection: hostname resolution failed: {e}")

    return url


class SafeUrlMetadataExtractor(MetadataExtractorProvider):
    """Secure metadata extractor with strict SSRF defense, timeouts, and size bounds."""

    def __init__(
        self,
        allowed_domains: Optional[List[str]] = None,
        timeout: float = 5.0,
        max_size: int = 1024 * 1024,
    ):
        self.allowed_domains = allowed_domains or settings.url_metadata_allowed_domains
        self.timeout = timeout
        self.max_size = max_size

    async def extract(self, url: str) -> Optional[ExtractedProductMetadata]:
        try:
            # 1. Validate destination against SSRF
            validated_url = validate_url_for_ssrf(url, self.allowed_domains)

            # 2. Fetch using httpx with strict timeout and manual redirect validation
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                current_url = validated_url
                max_redirects = 3
                for _ in range(max_redirects + 1):
                    response = await client.get(current_url)
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        # Re-validate redirect destination URL
                        current_url = validate_url_for_ssrf(location, self.allowed_domains)
                        continue
                    break

                if response.status_code != 200:
                    return None

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    return None

                # Enforce size limit
                content_bytes = response.content
                if len(content_bytes) > self.max_size:
                    return None

                html_text = content_bytes.decode("utf-8", errors="replace")

                # Parse meta tags safely
                parser = SimpleMetaHtmlParser()
                parser.feed(html_text)
                meta = parser.meta_tags

                title = meta.get("og:title") or meta.get("twitter:title")
                description = meta.get("og:description") or meta.get("description")
                image = meta.get("og:image") or meta.get("twitter:image")
                brand = meta.get("og:site_name")

                return ExtractedProductMetadata(
                    product_name=title,
                    brand=brand,
                    image_url=image,
                    description=description,
                )
        except Exception:
            # Safe degradation to demo catalogue on any error
            return None


def get_metadata_extractor() -> MetadataExtractorProvider:
    if settings.enable_url_metadata_extraction:
        return SafeUrlMetadataExtractor(
            allowed_domains=settings.url_metadata_allowed_domains,
            timeout=settings.url_metadata_timeout_seconds,
            max_size=settings.url_metadata_max_size_bytes,
        )
    return DisabledMetadataExtractor()
