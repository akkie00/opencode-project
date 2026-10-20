"""Live Azure prices for the AI Tech Stack Planner (Phase 4).

Where this module sits in the pipeline::

    rules.py      -> the recommended stack, with rough monthly estimates
    pricing.py    -> the same stack, with prices from the Azure price list
    recommender.py -> prints the recommendation

The numbers in rules.py are guesses.  "Azure App Service" is 30.0 because
somebody had to write *something* down, and that is good enough while the
rules pick the stack.  Phase 4 replaces the guess with the real price: the
Azure Retail Prices API is a public list of every meter in every region, it
needs no API key and no Azure login::

    GET https://prices.azure.com/api/retail/prices?$filter=serviceName eq 'Azure App Service' and armRegionName eq 'eastus'

The answer looks like this::

    {"Items": [{"meterName": "P1 v3 App", "retailPrice": 0.155,
                "unitOfMeasure": "1 Hour", ...}], "NextPageLink": null}

Three rules shape the code:

* **Nothing happens at import time.**  The only network call lives inside
  ``fetch_prices``, so importing this file is free and offline.
* **A price is a bonus, never a dependency.**  If the API cannot be reached,
  if a meter has been renamed or if the answer looks strange, the caller
  simply keeps the rough estimate from rules.py.  ``get_quotes`` never raises.
* **Every price comes with its assumption.**  A price without an assumption is
  just a fancier guess, so each entry in ``METER_SPECS`` carries a plain
  English note ("730 hours a month on a P1 plan") that the report can show.

Only the Python standard library is used, so there is nothing to install.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from rules import Component

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: The Azure region we price by default.  East US is the cheapest and the
#: most widely available region, so it is a fair default for an estimate.
DEFAULT_REGION = "eastus"

#: The public Azure Retail Prices API.  No API key, no login, no cost.
PRICING_API = "https://prices.azure.com/api/retail/prices"

#: The version of the cache file format.  Bump it when the layout changes,
#: so a cache written by an older version of the tool is ignored instead of
#: being read into nonsense.
CACHE_VERSION = 1

#: How long a cached price list stays fresh (hours).  Azure rarely changes
#: its prices, so a day old cache is as good as a new one - and it keeps the
#: tool from asking the API on every run.
CACHE_MAX_AGE_HOURS = 24

#: How long we wait for the price list before giving up (seconds).
FETCH_TIMEOUT = 15.0

#: How many pages of the answer we read at most.  The API hands out up to
#: 1,000 meters per page and links to the next page, so even the biggest
#: service needs only a few pages.  The limit is only there to stop a broken
#: link from looping forever.
MAX_PAGES = 20

#: How many hours a month has.  Used for every "always on" service (App
#: Service, Search, Redis): 365 days x 24 hours / 12 months = 730 hours.
HOURS_PER_MONTH = 730.0


class PricingError(Exception):
    """Raised when the price list cannot be read at all."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeterSpec:
    """How one component is priced from the Azure retail price list.

    component    - the component name used in rules.py, e.g. "Azure App Service"
    service_name - the Azure serviceName used in the API filter
    meter_matches - substrings that identify the meter we want
    unit_matches  - the unitOfMeasure values we accept, e.g. ("1 hour",)
    per_month    - how many of those units we assume a month uses
    note         - the assumption in plain English, shown in the report
    """

    component: str
    service_name: str
    meter_matches: tuple[str, ...]
    unit_matches: tuple[str, ...]
    per_month: float
    note: str


@dataclass(frozen=True)
class Quote:
    """One component priced for one month."""

    component: str
    monthly_usd: float
    unit: str
    meter: str
    note: str
    live: bool = True  # False when the meter could not be found


# ---------------------------------------------------------------------------
# The components we price, and the assumptions behind every price
# ---------------------------------------------------------------------------

# The meter names below were read out of the live price list, not guessed.
# Azure renames things from time to time, so each entry also says what the
# meter is and what we assume about the usage - that way a reader can tell
# whether the number still makes sense.
METER_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec(
        component="Azure OpenAI",
        # The price list calls this service "Foundry Models" these days; the
        # meters inside it still carry the product name "Azure OpenAI".
        service_name="Foundry Models",
        # A small chat model.  The first name is the cheapest one in the
        # list today; the other two are the classic GPT-4o mini, kept here
        # so the spec still works if the list changes back.  All of them are
        # plain *input* token meters - the cached and batch meters are
        # cheaper but they are not what a normal chat app pays.
        meter_matches=("gpt 5 nano inpt glbl", "gpt-4o-mini", "gpt 4o mini"),
        unit_matches=("1m",),
        # One million tokens a month.  Output tokens are billed separately
        # and are more expensive than input tokens.
        per_month=1.0,
        note=(
            "1 million input tokens a month for a small chat model. "
            "Output tokens cost extra and are not included here."
        ),
    ),
    MeterSpec(
        component="Azure App Service",
        service_name="Azure App Service",
        # "P1 v3 App" is the Premium v3 P1 plan.  The price list has a Linux
        # and a Windows version of it; the Linux one is the cheaper of the
        # two and is what the cheapest match picks.
        meter_matches=("p1 v3 app",),
        unit_matches=("1 hour",),
        # An app that is always on: 730 hours a month.
        per_month=HOURS_PER_MONTH,
        note=(
            "A Premium v3 P1 plan that runs 24/7 (730 hours a month). "
            "The Linux plan is the cheaper one; Windows costs about twice as much."
        ),
    ),
    MeterSpec(
        component="Azure Functions",
        service_name="Functions",
        # The Standard plan counts executions in batches of ten.
        meter_matches=("standard total executions",),
        unit_matches=("10",),
        # One million executions a month = 100,000 batches of ten.  The
        # Standard plan includes 1 million free executions a month, so this
        # meter is free until the allowance is used up (then it is about
        # $0.20 per extra million).
        per_month=100_000.0,
        note=(
            "1 million executions a month on the Standard plan. The plan "
            "includes 1 million free executions a month, so this is free "
            "until you go over the allowance (about $0.20 per extra million)."
        ),
    ),
    MeterSpec(
        component="Azure AI Search",
        # The price list still calls this service "Azure Cognitive Search".
        service_name="Azure Cognitive Search",
        # "Standard S1 Unit" is the standard S1 search unit.
        meter_matches=("standard s1 unit",),
        unit_matches=("1 hour",),
        # A search service that is always on: 730 hours a month.
        per_month=HOURS_PER_MONTH,
        note=(
            "One S1 search unit running 24/7 (730 hours a month). S1 holds a "
            "modest index; S2 costs about four times as much."
        ),
    ),
    MeterSpec(
        component="Azure AI Document Intelligence",
        # Document Intelligence lives with the other AI tools in the price list.
        service_name="Foundry Tools",
        # "S0 Pre-built Pages" is the Prebuilt model (text, tables and forms).
        # The plain "Read" model is cheaper but only extracts text.
        meter_matches=("s0 pre-built pages",),
        unit_matches=("1k",),
        # 1,000 pages a month.  The meter counts pages in thousands.
        per_month=1.0,
        note=(
            "1,000 pages a month with the Prebuilt model (text, tables and "
            "forms). The plain Read model is cheaper but only reads text."
        ),
    ),
    MeterSpec(
        component="Azure Blob Storage",
        service_name="Storage",
        # "Hot LRS Data Stored" is hot storage with a locally redundant copy
        # - the normal, cheap way to keep documents.
        meter_matches=("hot lrs data stored",),
        unit_matches=("1 gb/month",),
        # 100 GB of documents.
        per_month=100.0,
        note=(
            "100 GB of hot storage (with a local second copy). Cool and cold "
            "storage cost less per GB but add a fee when you read the data back."
        ),
    ),
    MeterSpec(
        component="Azure Cache for Redis",
        service_name="Redis Cache",
        # "C0 Cache" is the Basic C0 instance (250 MB) - the smallest cache
        # Azure sells.  "C0 Cache Instance" is the same size on the standard
        # tier and costs a little more.
        meter_matches=("c0 cache",),
        unit_matches=("1 hour",),
        # A cache that is always on: 730 hours a month.
        per_month=HOURS_PER_MONTH,
        note=(
            "A Basic C0 cache (250 MB) running 24/7 (730 hours a month). "
            "Bigger caches cost roughly ten times as much per hour."
        ),
    ),
    MeterSpec(
        component="Azure Key Vault",
        service_name="Key Vault",
        # "Operations" is the meter for reading and writing secrets and keys.
        meter_matches=("operations",),
        unit_matches=("10k",),
        # 10,000 operations a month.  The meter counts them in thousands.
        per_month=1.0,
        note=(
            "10,000 secret and key operations a month. Every read or write "
            "of a secret counts as one operation."
        ),
    ),
    MeterSpec(
        component="Azure Monitor",
        service_name="Azure Monitor",
        # "Basic Logs Data Ingestion" is the clean meter here: one price per
        # GB of logs that the platform swallows.  The other monitor meters
        # (analytics queries, search, data export) are billed separately and
        # are not part of this estimate.
        meter_matches=("basic logs data ingestion",),
        unit_matches=("1 gb",),
        # 10 GB of logs a month.
        per_month=10.0,
        note=(
            "10 GB of basic logs ingested a month. Log queries, alerts and "
            "data export are billed on top of this."
        ),
    ),
    # "Azure Private Endpoint" is NOT in the list above on purpose: the price
    # list we can read has no private endpoint meters at all (we looked for
    # every "private" and "endpoint" meter in the region and found none).
    # A wrong price is worse than no price, so this component keeps the
    # rough estimate from rules.py.
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return the value as a number (the default when it is missing or broken).

    A number written as an int comes back as an int from JSON, so every
    number is converted with float() to keep the types consistent.
    """
    if isinstance(value, bool):  # True/False are not numbers we want here
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


# ---------------------------------------------------------------------------
# Talking to the price list
# ---------------------------------------------------------------------------


def build_filter(spec: MeterSpec, region: str) -> str:
    """The OData filter for one component, e.g.
    "serviceName eq 'Azure OpenAI' and armRegionName eq 'eastus'"

    The API understands this small subset of OData: one or more fields with
    `eq`, joined with `and`.  Asking for one service in one region keeps the
    answer small.
    """
    return f"serviceName eq '{spec.service_name}' and armRegionName eq '{region}'"


def _get_json(url: str, timeout: float) -> dict:
    """Do one HTTP GET and return the parsed JSON body.

    Every problem (no network, timeout, error status, broken body) becomes a
    PricingError with a short reason, so the caller never has to deal with
    urllib or JSON exceptions.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise PricingError(
            f"the Azure price list replied with HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise PricingError(
            f"could not reach the Azure price list: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise PricingError("the request to the Azure price list timed out") from exc
    except OSError as exc:
        raise PricingError(f"could not reach the Azure price list: {exc}") from exc

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PricingError("the Azure price list did not return JSON") from exc

    if not isinstance(data, dict):
        raise PricingError("the Azure price list returned JSON that is not an object")

    return data


def _items_from_body(body: Any) -> list[dict]:
    """The price items inside one API answer.

    Raises PricingError when the answer does not look like the price list -
    a body we do not understand is better reported than guessed at.
    """
    if not isinstance(body, dict):
        raise PricingError("the price list answer was not a JSON object")

    items = body.get("Items")
    if not isinstance(items, list):
        raise PricingError("the price list answer had no 'Items' list")

    # Items that are not objects are skipped instead of breaking the loop.
    return [item for item in items if isinstance(item, dict)]


def _is_consumption(item: dict) -> bool:
    """True for a normal pay-as-you-go price.

    The price list also holds "DevTestConsumption" prices (cheaper, for
    dev/test subscriptions) and "Reservation" prices (a 1 or 3 year
    commitment, billed in a completely different way).  Neither is what a
    normal subscription pays, so both are skipped - otherwise the cheapest
    match would be a price nobody asked for.
    """
    kind = item.get("type")
    if kind is None:
        # A body without a type is taken at face value.
        return True
    return str(kind) == "Consumption"


def fetch_prices(
    specs: Sequence[MeterSpec] = METER_SPECS,
    region: str = DEFAULT_REGION,
    timeout: float = FETCH_TIMEOUT,
    opener: Callable[[str, float], dict] | None = None,
) -> dict[str, dict]:
    """Ask the Azure price list for the meters we need.

    Returns {component: price item dict} - the item is the one we picked for
    that component. A component whose meter is not in the answer is simply not
    in the returned dict.

    `opener(url, timeout) -> dict` does the HTTP GET and returns the parsed
    JSON body. The default implementation is the real request; tests inject a
    fake one. Raises PricingError when the request fails or the body is not
    the expected shape.

    The answer comes in pages of up to 1,000 meters, so the "next page" link
    is followed until the price list is exhausted (up to MAX_PAGES pages).
    Only the normal "Consumption" prices are used - dev/test and reservation
    prices are skipped (see _is_consumption).
    """
    get = opener if opener is not None else _get_json
    found: dict[str, dict] = {}

    for spec in specs:
        query = urllib.parse.urlencode(
            {"$filter": build_filter(spec, region)}, quote_via=urllib.parse.quote
        )
        url = f"{PRICING_API}?{query}"

        items: list[dict] = []
        next_url: str | None = url
        pages = 0
        while next_url and pages < MAX_PAGES:
            try:
                body = get(next_url, timeout)
            except PricingError:
                raise  # already a friendly message, keep it as it is
            except urllib.error.HTTPError as exc:
                raise PricingError(
                    f"the Azure price list replied with HTTP {exc.code}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # The default opener already turns these into a PricingError,
                # but an injected opener may raise them straight away.
                raise PricingError(
                    f"could not reach the Azure price list: {exc}"
                ) from exc

            items.extend(_items_from_body(body))
            # The link to the next page is either a full URL or nothing.
            next_link = body.get("NextPageLink")
            next_url = next_link if isinstance(next_link, str) and next_link else None
            pages += 1

        item = pick_item(spec, [i for i in items if _is_consumption(i)])
        if item is not None:
            found[spec.component] = item

    return found


# ---------------------------------------------------------------------------
# Picking the right meter out of the answer
# ---------------------------------------------------------------------------


def pick_item(spec: MeterSpec, items: list[dict]) -> dict | None:
    """The best price item for one spec, or None when nothing matches.

    An item matches when its meterName contains one of the meter_matches AND
    its unitOfMeasure matches one of the unit_matches (compare in lower case).
    When several items match, take the cheapest one - we assume the smallest
    sensible size.

    Both sides of the comparison are put in lower case first, so "P1 v3 App"
    is found by "p1 v3 app" and "1 Hour" by "1 hour".
    """
    wanted_units = {str(unit).lower() for unit in spec.unit_matches}
    wanted_meters = [str(part).lower() for part in spec.meter_matches]

    best: dict | None = None
    best_price = float("inf")

    for item in items or []:
        if not isinstance(item, dict):
            continue

        meter = str(item.get("meterName") or "").lower()
        unit = str(item.get("unitOfMeasure") or "").lower()

        if unit not in wanted_units:
            continue
        if not any(part in meter for part in wanted_meters):
            continue

        price = _as_float(item.get("retailPrice"), default=-1.0)
        if price < 0:
            # A price we cannot read is not a price - skip the meter.
            continue

        if price < best_price:
            best = item
            best_price = price

    return best


def monthly_price(item: dict, spec: MeterSpec) -> float:
    """The monthly USD estimate for one price item: retailPrice * per_month.

    The result is rounded to cents, because that is how a bill reads.
    """
    price = _as_float(item.get("retailPrice"), default=0.0)
    return round(price * float(spec.per_month), 2)


# ---------------------------------------------------------------------------
# The quotes the report uses
# ---------------------------------------------------------------------------


def get_quotes(
    region: str = DEFAULT_REGION,
    max_age_hours: int = CACHE_MAX_AGE_HOURS,
    fetcher: Callable[[Sequence[MeterSpec], str, float], dict[str, dict]] | None = None,
) -> dict[str, Quote]:
    """The quotes for every component we could price in `region`.

    Uses the on-disk cache when it is fresh enough, otherwise asks the API.
    A failure is NOT an error: the components come back as not priced (live=False)
    and the caller keeps its rough estimates.

    `fetcher(specs, region, timeout)` is injectable for tests; it defaults to
    `fetch_prices`.
    """
    cached = load_cache(region, max_age_hours)
    if cached is not None:
        items: dict[str, dict] = cached
    else:
        fetch = fetcher if fetcher is not None else fetch_prices
        try:
            items = fetch(METER_SPECS, region, FETCH_TIMEOUT)
        except PricingError:
            # The price list is a bonus, not a dependency.  When it cannot be
            # read we simply have no live prices.
            items = {}
        except Exception:
            # Same for anything else a fetcher might throw at us.
            items = {}
        if isinstance(items, dict) and items:
            save_cache(region, items)

    quotes: dict[str, Quote] = {}
    for spec in METER_SPECS:
        item = items.get(spec.component) if isinstance(items, dict) else None
        if not isinstance(item, dict):
            # No meter for this component - it keeps the rough estimate.
            quotes[spec.component] = Quote(
                component=spec.component,
                monthly_usd=0.0,
                unit="",
                meter="",
                note=spec.note,
                live=False,
            )
            continue

        quotes[spec.component] = Quote(
            component=spec.component,
            monthly_usd=monthly_price(item, spec),
            unit=str(item.get("unitOfMeasure") or ""),
            meter=str(item.get("meterName") or ""),
            note=spec.note,
            live=True,
        )

    return quotes


def apply_quotes(stack: Iterable[Component], quotes: dict[str, Quote]) -> tuple[list, str]:
    """Return the stack with live prices where we have them.

    `stack` is a list of rules.Component. Returns (new stack, source) where
    `source` is "live" when at least one component was priced from the Azure
    price list and "estimate" otherwise. The components are COPIES with an
    updated cost_month - the frozen originals are never changed.
    """
    priced: list[Component] = []
    live = False

    for component in stack or []:
        quote = quotes.get(component.name) if isinstance(quotes, dict) else None
        if quote is None or not getattr(quote, "live", False):
            # No live price for this one - it keeps its rough estimate.
            priced.append(replace(component))
            continue

        live = True
        priced.append(replace(component, cost_month=round(float(quote.monthly_usd), 2)))

    return priced, ("live" if live else "estimate")


# ---------------------------------------------------------------------------
# The on-disk cache
# ---------------------------------------------------------------------------


def cache_path() -> Path:
    """The cache file: <repository root>/.cache/azure-prices.json.

    The repository root is two levels above this file
    (src/ -> ai-tech-stack-planner/ -> repository root).
    """
    return Path(__file__).resolve().parents[2] / ".cache" / "azure-prices.json"


def load_cache(region: str, max_age_hours: int = CACHE_MAX_AGE_HOURS) -> dict | None:
    """The cached price items for a region, or None when there is no fresh cache.

    The cache is ignored when the file is missing, unreadable, written by a
    different version of the tool, filled for a different region or older
    than `max_age_hours`.
    """
    path = cache_path()

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # No file (or a file we cannot read) means no cache.
        return None

    try:
        data = json.loads(text)
    except ValueError:
        # A broken cache is not worth complaining about - it is only a
        # shortcut for the next request.
        return None

    if not isinstance(data, dict):
        return None
    if data.get("cache_version") != CACHE_VERSION:
        return None
    if data.get("region") != region:
        return None

    items = data.get("items")
    if not isinstance(items, dict):
        return None

    fetched_at = _parse_time(data.get("fetched_at"))
    if fetched_at is None:
        return None

    age = datetime.now() - fetched_at
    if age > timedelta(hours=max_age_hours):
        # Too old to trust.
        return None

    return items


def save_cache(region: str, data: dict) -> None:
    """Write the price items to the cache file. A failure to write is ignored -
    the cache is only an optimisation."""
    path = cache_path()
    payload = {
        "cache_version": CACHE_VERSION,
        "region": region,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "items": data,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        # A cache we cannot write is not a problem - the next run simply
        # asks the API again.
        pass


def _parse_time(value: Any) -> datetime | None:
    """Read an ISO 8601 timestamp, or None when it cannot be read."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None
