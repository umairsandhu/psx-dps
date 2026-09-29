# Symbols: what a PSX ticker actually tells you

Everything learned about PSX's symbol universe while building this. Counts
are from a live pull on 2026-09-29 and will drift; the *conventions* are the
durable part.

```python
from psx_dps import Client
Client().symbols(include_debt=True)     # [{symbol, name, sectorName, isETF, isDebt}]
```

---

## The headline trap: two different universes

| Source | Count | What it is |
|---|---|---|
| `/symbols` | **1,029** | Everything **listed** |
| `/market-watch` | **496** | Everything **trading today** |

**534 listed instruments are not in the market watch.** That is normal, not
an error: the watch carries what is actually on the board. Most of the
absentees are government debt (which trades rarely), plus suspended,
delisted and non-regular-board lines.

This is why `quote()` raises two different errors:

```python
psx.quote("MARI")      # fine
psx.quote("ENGRO")     # NoData        - listed, not trading today
psx.quote("NOTREAL")   # UnknownSymbol - not a PSX symbol at all
```

If your app treats "absent from market watch" as "bad symbol", you will
throw away roughly half the universe.

## Composition

| | Count |
|---|---|
| Equities | 755 |
| Debt instruments (`isDebt`) | 265 |
| ETFs (`isETF`) | 9 |
| No `sectorName` | 53 |

Trust the **`isDebt` / `isETF` flags**, not the symbol text. They are what
the library filters on, and `symbols()` excludes debt by default.

The 53 with an empty `sectorName` are all equities and none are debt or
ETFs — they are off-regular-board lines (rights issues, suspended
companies, modarabas). Do not assume `sectorName` is always populated.

---

## Government debt is machine-readable

The largest and most structured group. The format is
`P<nn><TYPE><DDMMYY>`, and **the last six digits are the maturity date**.

Verified against all 232 government instruments: every one parsed as a
valid date, and the `name` field confirms it independently.

```
P01GHS020927   ->  2027-09-02   "1 Yr Dis GoP Hybrid Sukuk Mat-02-Sep-27"
PK01TB011026   ->  2026-10-01   "1-Month Treasury Bill Mat - 01-Oct-26"
P02PIB150128   ->  2028-01-15   "2-Year PIB Fixed Mat. Date 15-Jan-28"
```

```python
import re, datetime as dt

def maturity(symbol):
    """Maturity date of a government debt symbol, or None."""
    m = re.match(r"^P[KX]?\d{2}[A-Z]{2,3}(\d{2})(\d{2})(\d{2})$", symbol)
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    try:
        return dt.date(2000 + y, mth, d)
    except ValueError:
        return None
```

### Instrument types

| Code | Count | Instrument |
|---|---|---|
| `TB` | 59 | Treasury Bill |
| `PFL` | 46 | PIB Floater |
| `PIB` | 36 | Pakistan Investment Bond (fixed) |
| `FRR` | 30 | Fixed Rental Rate (Ijara sukuk) |
| `VRR` | 25 | Variable Rental Rate |
| `GHS` | 21 | GoP Hybrid Sukuk |
| `GIS` | 7 | GoP Ijara Sukuk |
| `FRZ` | 6 | Fixed Rental Rate, zero coupon |
| `PFA` | 1 | PIB Floater (long) |
| `GVR` | 1 | Green Variable Rate |

The `P01` / `P02` / `P03` / `P10` prefix tracks the tenor family (roughly
years to maturity at issue), so the same issuer and type appear under
several prefixes.

---

## Corporate instruments

| Pattern | Count | Meaning | Example |
|---|---|---|---|
| `<ISSUER>TFC<n>` | 21 | Term Finance Certificate | `AKBLTFC6` — Askari Bank (TFC6) |
| `<ISSUER>SC<n>` | 4 | Sukuk Certificate | `KELSC5` — K-Electric (Sukuk5) |
| `<ISSUER>PS` / `CPS` | 10 | Preference shares | `AGLNCPS` — Agritech Non-Voting (Pref) |
| `<ISSUER>ETF` | 9 | Exchange-traded fund | `MZNPETF` — Meezan Pakistan ETF |

All nine ETFs happen to end in `ETF`, but use `isETF` — a naming convention
is not a guarantee.

### Do not infer "rights" from the symbol

Tempting and wrong. A trailing `R` is not reliable:

```
786R    786 Investment (Right)          <- a rights issue
ASCR1   AL-Shaheer (R)                  <- a rights issue
BRR     B.R.R. Guardian Modaraba        <- NOT a rights issue
DCR     Dolmen City REIT                <- NOT a rights issue
```

The reliable signal is the **name**, which carries `(R)` or `(Right)`:

```python
is_rights = "(R)" in name or "(Right" in name
```

---

## Symbols change, and change silently

Corporate actions rename and retire tickers. There is no redirect and no
error — the old symbol simply stops appearing in the market watch:

```
ENGRO  ->  ENGROH     Engro Corporation became Engro Holdings after the
                      2024 Dawood Hercules / Engro scheme of arrangement
```

A tracker that pinned `ENGRO` at setup has been quietly recording nothing
since. Practical defences:

- Reconcile your watchlist against `symbols()` on a schedule, and alert on
  anything that disappears — do not fail silently.
- Store an internal ID next to the ticker, so a rename is a mapping change
  and not a break in your history.
- `search()` finds the successor by company name when a ticker vanishes:
  ```python
  psx.search("engro")   # EFERT, ENGROH, EPCL, EPQL, FCEPL ...
  ```

---

## Sector codes

`/market-watch` gives a numeric `sector` code, not a name. The mapping comes
from `sector_summary()`:

```python
{r["SECTOR CODE"]: r["SECTOR NAME"] for r in psx.sector_summary()}
```

| Code | Sector |
|---|---|
| `0801` | Automobile Assembler |
| `0802` | Automobile Parts & Accessories |
| `0803` | Cable & Electrical Goods |
| `0804` | Cement |
| `0805` | Chemical |
| `0806` | Close - End Mutual Fund |
| `0807` | Commercial Banks |
| `0808` | Engineering |
| `0809` | Fertilizer |
| `0810` | Food & Personal Care Products |
| `0811` | Glass & Ceramics |
| `0812` | Insurance |
| `0813` | Inv. Banks / Inv. Cos. / Securities Cos. |
| `0814` | Jute |
| `0815` | Leasing Companies |
| `0816` | Leather & Tanneries |
| `0818` | Miscellaneous |
| `0819` | Modarabas |
| `0820` | Oil & Gas Exploration Companies |
| `0821` | Oil & Gas Marketing Companies |
| `0822` | Paper, Board & Packaging |
| `0823` | Pharmaceuticals |
| `0824` | Power Generation & Distribution |
| `0825` | Refinery |
| `0826` | Sugar & Allied Industries |
| `0827` | Synthetic & Rayon |
| `0828` | Technology & Communication |
| `0829` | Textile Composite |
| `0830` | Textile Spinning |
| `0831` | Textile Weaving |
| `0832` | Tobacco |
| `0833` | Transport |
| `0834` | Vanaspati & Allied Industries |
| `0835` | Woollen |
| `0836` | Real Estate Investment Trust |
| `0837` | Exchange Traded Funds |
| `0838` | Property |
| `0839` | Apparel |

`/symbols` gives `sectorName` directly, so you only need the code mapping
when joining against market-watch rows.

---

## Index membership

`/market-watch` rows carry a `listed` column: a comma-separated list of
every index and board the symbol belongs to. 17 distinct tags.

| Tag | Members | |
|---|---|---|
| `ALLSHR` | 487 | All Share |
| `KMIALLSHR` | 301 | Shariah-compliant All Share |
| `KSE100` / `KSE100PR` | 100 | The headline index / price-return variant |
| `KSE30` | 30 | Top 30 by free float |
| `KMI30` | 30 | Shariah-compliant 30 |
| `MII30` | 30 | Meezan Islamic 30 |
| `PSXDIV20` | 20 | Dividend 20 |
| `ACI` | 20 | Alfalah Consumer Index |
| `NBPPGI` / `NITPGI` / `MZNPI` | 15 / 13 / 12 | Fund-sponsored indices |

Membership is the cheapest possible filter — it costs nothing extra because
it is already in the market watch you fetched:

```python
kse100 = [r for r in psx.market_watch() if "KSE100" in r["listed"].split(",")]
```

`index_constituents("KSE100")` gives the same 100 names *with index weights
and free float*, which the market watch does not carry.

**A Shariah-screening note:** `KMIALLSHR` / `KMI30` membership reflects
PSX's own screening. It is a reasonable starting filter, not a substitute
for your own compliance process.

---

## Quick reference

```python
from psx_dps import Client

with Client() as psx:
    psx.symbols()                        # 755 equities (debt excluded)
    psx.symbols(include_debt=True)       # all 1,029
    psx.symbols(etf_only=True)           # the 9 ETFs
    psx.search("cement")                 # by symbol or company name
    psx.index_constituents("KSE100")     # with weights and free float
```

| Question | Use |
|---|---|
| Is this a real PSX symbol? | `symbols(include_debt=True)` |
| Is it trading today? | presence in `market_watch()` |
| Is it debt / an ETF? | the `isDebt` / `isETF` flags |
| When does this bond mature? | parse the symbol, or read `name` |
| Is it in the KSE100? | the `listed` column |
| What sector is it? | `sectorName`, or the code map above |
| Is it a rights issue? | `(R)` in the **name** |
| Where did my symbol go? | `search()` by company name |
