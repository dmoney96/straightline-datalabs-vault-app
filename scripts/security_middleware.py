"""
Security middleware for Straightline Vault
Phase 1: Headers + rate limiting (always on)
Phase 2: Content filtering (disabled by default)
"""
from __future__ import annotations

import os
import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Feature flags
CONTENT_FILTERING_ENABLED = os.getenv("ENABLE_CONTENT_FILTERING", "0") == "1"

# Blocklist paths (only used if filtering enabled)
BLOCKLIST_DIR = Path("/var/secure/blocklists")
CSAM_HASHES_FILE = BLOCKLIST_DIR / "csam_hashes.txt"
PHISHING_DOMAINS_FILE = BLOCKLIST_DIR / "phishing_domains.txt"

# In-memory blocklists
CSAM_BLOCKLIST_HASHES: set[str] = set()
PHISHING_DOMAINS: set[str] = set()


def load_blocklists():
    """Load blocklists from disk (only if filtering enabled)"""
    if not CONTENT_FILTERING_ENABLED:
        logging.info("Content filtering DISABLED - blocklists not loaded")
        return
    
    global CSAM_BLOCKLIST_HASHES, PHISHING_DOMAINS
    
    logger = logging.getLogger(__name__)
    logger.warning("Content filtering ENABLED - loading blocklists")
    
    # Create directories if they don't exist
    BLOCKLIST_DIR.mkdir(parents=True, exist_ok=True)
    
    # CSAM hashes
    if not CSAM_HASHES_FILE.exists():
        CSAM_HASHES_FILE.touch()
        logger.warning(f"Created empty CSAM blocklist: {CSAM_HASHES_FILE}")
        logger.warning("IMPORTANT: Apply for IWF membership to populate this file")
    
    try:
        hashes = set(
            line.strip().lower()
            for line in CSAM_HASHES_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
        CSAM_BLOCKLIST_HASHES = hashes
        logger.info(f"Loaded {len(hashes)} CSAM hashes")
    except Exception as e:
        logger.error(f"Failed to load CSAM blocklist: {e}")
    
    # Phishing domains
    if not PHISHING_DOMAINS_FILE.exists():
        PHISHING_DOMAINS_FILE.touch()
        logger.warning(f"Created empty phishing blocklist: {PHISHING_DOMAINS_FILE}")
    
    try:
        domains = set(
            line.strip().lower()
            for line in PHISHING_DOMAINS_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
        PHISHING_DOMAINS = domains
        logger.info(f"Loaded {len(domains)} phishing domains")
    except Exception as e:
        logger.error(f"Failed to load phishing blocklist: {e}")


def check_content_hash(content: bytes) -> tuple[bool, str | None]:
    """
    Check content hash against CSAM blocklist.
    
    Returns: (is_blocked, reason)
    
    If filtering disabled, always returns (False, None)
    """
    if not CONTENT_FILTERING_ENABLED:
        return False, None
    
    if not CSAM_BLOCKLIST_HASHES:
        # No hashes loaded - don't block but log
        logging.debug("CSAM blocklist empty - no filtering performed")
        return False, None
    
    sha256 = hashlib.sha256(content).hexdigest().lower()
    md5 = hashlib.md5(content).hexdigest().lower()
    
    if sha256 in CSAM_BLOCKLIST_HASHES or md5 in CSAM_BLOCKLIST_HASHES:
        # CRITICAL: Log and report
        security_logger = logging.getLogger("security")
        security_logger.critical(
            f"CSAM_DETECTION hash_match sha256={sha256[:8]}... "
            f"REPORTING_REQUIRED"
        )
        return True, "prohibited_content"
    
    return False, None


def check_domain_blocklist(url: str) -> tuple[bool, str | None]:
    """
    Check if domain is in phishing blocklist.
    
    Returns: (is_blocked, reason)
    
    If filtering disabled, always returns (False, None)
    """
    if not CONTENT_FILTERING_ENABLED:
        return False, None
    
    if not PHISHING_DOMAINS:
        return False, None
    
    domain = urlparse(url).netloc.lower()
    
    if domain in PHISHING_DOMAINS:
        logging.warning(f"SECURITY: Blocked phishing domain: {domain}")
        return True, "phishing_site"
    
    return False, None


def init_security(app: Flask):
    """
    Initialize security middleware.
    
    Always enables: headers, rate limiting
    Conditionally enables: content filtering (if ENABLE_CONTENT_FILTERING=1)
    
    Returns: limiter instance
    """
    logger = logging.getLogger(__name__)
    
    # Always load blocklists (but they won't be used unless flag is set)
    load_blocklists()
    
    # Security headers (always on)
    Talisman(
        app,
        force_https=False,  # nginx handles HTTPS
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        content_security_policy={
            'default-src': "'self'",
            'script-src': "'self'",
            'style-src': ["'self'", "'unsafe-inline'"],
            'img-src': "'self' data:",
            'frame-ancestors': "'none'",
            'base-uri': "'self'",
            'form-action': "'self'",
        },
        session_cookie_secure=True,
        session_cookie_samesite='Lax',
    )
    logger.info("✓ Security headers enabled (Flask-Talisman)")
    
    # Rate limiting (always on)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri=redis_url,
    )
    logger.info(f"✓ Rate limiting enabled (Redis: {redis_url})")
    
    # Log content filtering status
    if CONTENT_FILTERING_ENABLED:
        logger.warning("⚠ Content filtering ENABLED")
    else:
        logger.info("○ Content filtering DISABLED (set ENABLE_CONTENT_FILTERING=1 to enable)")
    
    return limiter
