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


class CoolingDown(PSXError):
    """We are deliberately not sending a request right now.

    PSX recently told us to slow down (an HTTP 429 or 503), or repeatedly
    failed, so this client is standing down for a while. Nothing is broken
    and you are not banned -- this is the library choosing to be quiet.

    Think of it as the trip switch in a fuse box. When something draws too
    much current the switch opens, the circuit goes dead on purpose, and you
    wait before closing it again. Hammering the switch back on is how you
    start a fire. Same idea here: the alternative to backing off is retrying
    into a server that already said no, which is what actually gets clients
    blocked.

    The stand-down is shared by every psx-dps process on the machine, so one
    project's rejection quiets the others too.

    Handle it by skipping this cycle, not by retrying around it:

        try:
            snap = psx.snapshot()
        except CoolingDown as exc:
            log.warning("PSX asked us to back off: %s", exc)
            return

    Cached data is still served while this is in effect, so an app that
    already has data keeps working -- it just refreshes less often.

    `CircuitOpen` is an alias for this class, after the "circuit breaker"
    pattern this implements.
    """


#: The standard name for this pattern, for anyone who knows it by that.
CircuitOpen = CoolingDown
