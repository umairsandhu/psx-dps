"""Exception hierarchy for psx-dps.

Everything raised by this package derives from PSXError, so a caller can
wrap all of it in one `except` without catching unrelated OSErrors.
"""


class PSXError(Exception):
    """Base class for every error this package raises."""


class NoHealthyNode(PSXError):
    """No dps.psx.com.pk node served the data routes.

    Usually means PSX renumbered its nodes and both the DNS answer and our
    remembered pool are stale. `psx doctor` prints the full picture; the
    PSX_NODE env var forces a specific address.
    """


class TransportError(PSXError):
    """A request failed at the network/HTTP layer after retries."""


class UpstreamError(PSXError):
    """PSX answered, but with an error status or an unusable body."""


class UnknownSymbol(PSXError):
    """PSX does not recognise the requested symbol."""


class NoData(PSXError):
    """The request was valid but PSX returned nothing.

    Routinely means a non-trading day (weekend/holiday) rather than a fault,
    so it is a distinct type callers can quietly skip.
    """


class RateLimited(PSXError):
    """A local guard rail tripped, not a PSX response.

    Raised when the configured request budget is exhausted, so runaway loops
    fail loudly here instead of quietly hammering PSX.
    """


class CircuitOpen(PSXError):
    """PSX signalled distress recently, so we are deliberately standing down.

    Raised instead of sending a request while a cooldown is in effect. The
    cooldown is shared by every process on the machine: if one project gets
    a 429, the others stop too, rather than each discovering it the hard way.

    Treat this as "skip this cycle", not as a failure to retry around.
    """
