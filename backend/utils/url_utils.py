import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException


def normalized_url(raw: str) -> str:
    """Validate and normalize a URL, rejecting private/non-public destinations."""
    p = urlparse(raw)
    if p.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http and https URLs are allowed.")
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise HTTPException(status_code=400, detail="This hostname is not allowed.")
    try:
        answers = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        ips = {ipaddress.ip_address(item[4][0]) for item in answers}
    except (socket.gaierror, ValueError, OSError):
        raise HTTPException(status_code=400, detail="Hostname could not be resolved.")
    if not ips or any(not ip.is_global for ip in ips):
        raise HTTPException(status_code=400, detail="Private or non-public network destinations are not allowed.")
    clean_path = p.path or "/"
    return urlunparse((p.scheme, p.netloc, clean_path, "", p.query, ""))


def domain_of(url: str) -> str:
    """Extract the domain from a URL, stripping www. prefix."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host
