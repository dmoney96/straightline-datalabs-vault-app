import os
from urllib.parse import urlparse

print("HEADLESS_FETCH_ENABLED =", os.getenv("HEADLESS_FETCH_ENABLED"))
print("HEADLESS_FETCH_ON_403  =", os.getenv("HEADLESS_FETCH_ON_403"))
print("HEADLESS_ALLOWED_DOMAINS =", (os.getenv("HEADLESS_ALLOWED_DOMAINS") or "")[:200])

# Import from your scripts dir
from headless_fetch import headless_can_use_for

tests = [
    "https://courtlistener.com/",
    "https://www.courtlistener.com/",
    "https://subdomain.courtlistener.com/",
]
for u in tests:
    host = urlparse(u).hostname or ""
    print(u, "=>", host, "allowed?", headless_can_use_for(host))
