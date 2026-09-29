# Staying unblocked

PSX lets us read this data. They have no obligation to, and no contract with
us. The whole arrangement rests on us not becoming a problem. This document
is how that is enforced — partly by the library, partly by you.

---

## Why clients get blocked

Almost never for volume. Steady, modest traffic is invisible.

They get blocked for **what they do when the server is in trouble**:

1. PSX has a wobble and starts returning errors or refusing connections.
2. Every client treats that as a transient failure and **retries**.
3. Retries arrive faster than normal traffic, because retries don't wait
   for the next scheduled poll.
4. The wobble becomes an outage.
5. An operator looks at the logs, finds the loudest source, and blocks it.

The client that gets blocked is not the one making the most requests on a
normal day. It is the one making the most requests **on the worst day**.

So the rule the whole design follows:

> **When PSX pushes back, go quieter — not louder.**

---

## What a "cooldown" is

When PSX tells us to slow down, the library stops sending requests for a
while. That pause is a cooldown, and while it is in effect any call that
would hit the network raises `CoolingDown` instead.

**The fuse-box analogy.** When a circuit draws too much current, the trip
switch opens. The circuit goes dead *on purpose*. You wait, you fix what was
overloading it, then you close the switch. Standing there flipping it back
on repeatedly is how you start a fire.

`CoolingDown` is that trip switch. Nothing is broken, you are not banned —
the library is choosing to be quiet so that you don't have to be banned.

This is the standard "circuit breaker" pattern, and `CircuitOpen` is an
alias of the same class if you know it by that name. Both work:

```python
from psx_dps import CoolingDown     # or CircuitOpen - identical
```

### What it looks like

```
psx_dps.errors.CoolingDown: standing down for another 59s after 1
rejection(s) from PSX (last: HTTP 429 on /market-watch). This is a
deliberate cooldown shared by every psx-dps process on this machine --
skip this cycle rather than retrying around it.
```

### Three things it is not

- **Not a ban.** PSX has not blocked you. This is self-imposed.
- **Not a bug.** It is the library working correctly.
- **Not something to retry around.** Retrying is the behaviour it exists to
  prevent.

---

## What the library does automatically

| What PSX does | What happens |
|---|---|
| `429 Too Many Requests` | Stop at once, **no retries**, start a cooldown |
| `503 Service Unavailable` | Same |
| Sends a `Retry-After` header | Honoured, and it wins even if longer than ours |
| `500`, `502`, `504` | A few jittered retries, then a cooldown |
| Connections repeatedly fail | Cooldown, so the next cron run doesn't walk in |

### The cooldown escalates, then forgives

```
   1st rejection  ->  60s     quiet
   2nd            ->   5 min
   3rd            ->  15 min
   4th and after  ->   1 hour   (capped, never grows further)

   15 clean minutes  ->  strikes reset to zero
```

A single blip costs you a minute. A sustained problem costs an hour and
stops being PSX's problem. Old blips are forgiven, so today's first hiccup
is never expensive because of something last week.

### It is shared between processes

The cooldown lives in a file in the shared cache directory, not in memory.
If your tracker gets a 429, your dashboard and your notebook **also** stand
down. Otherwise each one discovers the limit separately and you send three
times the traffic PSX was already complaining about.

### Your app keeps working

The cache is checked **before** the cooldown. An app that already has data
keeps serving it; it just refreshes less often. A cooldown slows you down —
it does not blind you.

---

## How to handle it in your code

Skip the cycle. That is the entire correct response.

```python
from psx_dps import Client, CoolingDown

def poll():
    try:
        snapshot = psx.snapshot()
    except CoolingDown as exc:
        log.warning("PSX asked us to back off: %s", exc)
        return                      # no retry, no sleep-and-try-again
    store(snapshot)
```

**Do not** do any of these:

```python
except CoolingDown:
    time.sleep(60); poll()          # defeats the entire mechanism
except CoolingDown:
    pass                            # silent - you'll never know it happened
except CoolingDown:
    subprocess.run(["psx-dps", "cooldown", "--clear"])   # actively harmful
except Exception:
    retry()                         # swallows it along with everything else
```

---

## What the library cannot do for you

Six rules that need a human.

**1. Alert on it.** `CoolingDown` is your early warning that your usage has
become visible to PSX. Log it at WARNING and page on repeats. One a month is
noise. Several a day means fix your usage now, before someone at PSX does.

**2. Never route around a block.** No proxy rotation. No rotating
User-Agents. No browser fingerprint spoofing. No clearing the cooldown to
keep polling. This is the line between *a reader PSX tolerates* and
*something PSX is actively trying to stop* — and crossing it is what turns a
temporary throttle into a permanent ban.

**3. Stay contactable.** Name your project and give a way to reach you:

```python
Client(user_agent="markaz-psx-tracker/1.0 (+https://markaz.app/contact)")
```

An operator who can see what you are and email you will email you. One
looking at anonymous traffic pretending to be Chrome will just block it.

**4. One writer, many readers.** Run a single poller that fetches and writes
to your database. Dashboards, notebooks and reports read *your database*,
not PSX. Five services polling independently is five times the footprint for
identical data.

**5. Stagger your schedule.** Don't fire exactly on the 5-minute boundary
from several hosts — offset each by a random few seconds.

```cron
*/5 * * * * sleep $((RANDOM \% 30)); /srv/tracker/poll.py
```

**6. Fail soft.** If PSX is unreachable, show stale data with its timestamp.
Every snapshot carries `captured_at` for exactly this. Never show a spinner
that retries in a loop.

---

## Runbook: I got a CoolingDown

**1. Look at why.**

```bash
psx-dps cooldown
```

```
STANDING DOWN for 275s
strikes  2
reason   HTTP 429 on /market-watch
```

**2. Do nothing for now.** It expires on its own. Your next scheduled poll
picks up normally.

**3. Work out what caused it** before the next one. Nearly always one of:

| Cause | Fix |
|---|---|
| A per-symbol call in a loop | Use `snapshot()` / `market_watch()` — one request |
| `force_refresh=True` on a timer | Remove it; let the TTLs work |
| Several services polling separately | One writer, many readers |
| Cache directory not shared | Point them all at `~/.cache/psx-dps` |
| A retry loop around a failure | Delete it; the library already retries |
| Genuinely PSX having a bad day | Nothing — this is the system working |

**4. Only clear it if you have actually fixed the cause.**

```bash
psx-dps cooldown --clear
```

Clearing it to keep polling is how a warning becomes a block.

---

## Weekly self-audit

```python
with Client() as psx:
    print(psx.health())
```

| Field | Healthy for a 5-minute tracker |
|---|---|
| `budget_used_24h` | a few hundred — a 5-min sweep is ~432/day |
| `cooldown.strikes` | `0` |
| `cooldown.cooling_down` | `False` |
| `upstream_requests` | ~6 per poll cycle, **flat over time** |
| `cache.hit_rate` | high, if anything else reads through the same client |

A request count that climbs week over week means something is looping. Find
it before PSX does.

```bash
psx-dps doctor     # node, session, budget and cooldown in one view
```

---

## A note on what was tested

The back-off logic is covered by tests against simulated responses —
escalation, `Retry-After`, cross-process sharing, and cache-still-served
while cooling down.

We did **not** deliberately trigger a real 429 from PSX to verify it. Doing
that would be precisely the abuse this document exists to prevent. So the
handling is proven; the exact shape of PSX's real 429 response is not. The
code handles both the presence and the absence of a `Retry-After` header.
