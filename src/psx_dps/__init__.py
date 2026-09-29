"""psx-dps -- an unofficial Python client for the Pakistan Stock Exchange
data portal (dps.psx.com.pk).

    from psx_dps import Client

    with Client() as psx:
        print(psx.quote("MARI"))
        for bar in psx.eod("LUCK", since="2026-01-01"):
            print(bar)

Not affiliated with or endorsed by the Pakistan Stock Exchange. It reads the
same undocumented endpoints the portal's own front end uses; they can change
without notice. Please read docs/FAIR-USE.md before wiring this into
anything that runs on a schedule.
"""

from .cache import Cache
from .client import ANNOUNCEMENT_TYPES, INDEX_CODES, Client
from .errors import (
    CircuitOpen,
    CoolingDown,
    NoData,
    NoHealthyNode,
    PSXError,
    RateLimited,
    TransportError,
    UnknownSymbol,
    UpstreamError,
)
from .parsing import to_number
from .ratelimit import Breaker, Throttle
from .transport import HOST, Transport

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Cache",
    "Throttle",
    "Breaker",
    "Transport",
    "HOST",
    "ANNOUNCEMENT_TYPES",
    "INDEX_CODES",
    "to_number",
    "PSXError",
    "NoHealthyNode",
    "TransportError",
    "UpstreamError",
    "UnknownSymbol",
    "NoData",
    "RateLimited",
    "CoolingDown",
    "CircuitOpen",
    "__version__",
]
