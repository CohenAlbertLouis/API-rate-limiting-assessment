"""R2 - the rate limiting is our own: no rate-limiting library is installed."""
from pathlib import Path

RATE_LIMIT_LIBRARIES = {"slowapi", "limits", "fastapi-limiter", "ratelimit", "ratelimiter", "pyrate-limiter", "aiolimiter"}


def test_no_rate_limiting_library_in_requirements():
    names = set()
    for f in ("requirements.txt", "requirements-dev.txt"):
        for line in Path(f).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-r")):
                names.add(line.split("==")[0].split("[")[0].strip().lower())
    assert names and not names & RATE_LIMIT_LIBRARIES
