"""Tests for the live Azure prices (ai-tech-stack-planner/src/pricing.py).

Phase 4 adds real cost data: the Azure components in the recommended stack
are priced from the public Azure retail price list instead of the rough
estimates that live in rules.py.

Nothing in here touches the network.  Every request goes through an injected
fake opener or fetcher, and the on-disk cache is pointed at a temporary
folder, so the tests only pin down the contract of the module.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pricing  # noqa: E402  (import after the sys.path tweak)
from rules import Component  # noqa: E402  (import after the sys.path tweak)


#: A meter spec used by most of the tests.
SAMPLE_SPEC = pricing.MeterSpec(
    component="Azure App Service",
    service_name="Azure App Service",
    meter_matches=("p1 v3 app",),
    unit_matches=("1 hour",),
    per_month=730.0,
    note="A P1 plan that runs all month.",
)


def price_item(meter="P1 v3 App", unit="1 Hour", price=0.155, **extra):
    """One price item as the API returns it."""
    item = {
        "meterName": meter,
        "retailPrice": price,
        "unitOfMeasure": unit,
        "serviceName": "Azure App Service",
        "armRegionName": "eastus",
        "currencyCode": "USD",
        "type": "Consumption",
        "productName": "Azure App Service Premium v3 Plan - Linux",
        "skuName": "P1 v3",
    }
    item.update(extra)
    return item


class FakeOpener:
    """A fake opener: records the calls and returns a canned body.

    The opener is called as ``opener(url, timeout)``.  Both positional and
    keyword calls are accepted so that the tests do not depend on the exact
    call style of the module.
    """

    def __init__(self, body=None, error=None):
        self.body = body if body is not None else {"Items": [], "NextPageLink": None}
        self.error = error
        self.calls = []

    def __call__(self, url, timeout=0.0, *args, **kwargs):
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        if callable(self.body):
            return self.body(url)
        return self.body


def fake_fetcher(items=None, error=None):
    """A fake fetcher for get_quotes: fetcher(specs, region, timeout).

    It records the call and returns {component: price item}.  When `error` is
    given the fetcher raises it instead - that is how a broken price list
    looks from the outside.
    """
    calls = []

    def fetch(specs, region, timeout):
        calls.append((list(specs), region, timeout))
        if error is not None:
            raise error
        return dict(items or {})

    fetch.calls = calls
    return fetch


class TempCacheTestCase(unittest.TestCase):
    """A test case with the price cache pointed at a temporary folder.

    The real cache lives in the repository, so every test that reads or
    writes it moves it somewhere harmless first.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_file = Path(self.tmp.name) / "azure-prices.json"

        original = pricing.cache_path

        def fake_cache_path():
            return self.cache_file

        pricing.cache_path = fake_cache_path
        self.addCleanup(setattr, pricing, "cache_path", original)


class TestBuildFilter(unittest.TestCase):
    """The OData filter that asks the API for one service in one region."""

    def test_the_filter_names_the_service_and_the_region(self):
        filter_text = pricing.build_filter(SAMPLE_SPEC, "eastus")

        self.assertEqual(
            filter_text,
            "serviceName eq 'Azure App Service' and armRegionName eq 'eastus'",
        )

    def test_the_filter_follows_the_spec_and_the_region(self):
        spec = pricing.MeterSpec(
            component="Azure Blob Storage",
            service_name="Storage",
            meter_matches=("hot lrs data stored",),
            unit_matches=("1 gb/month",),
            per_month=100.0,
            note="100 GB of hot storage.",
        )

        self.assertEqual(
            pricing.build_filter(spec, "westeurope"),
            "serviceName eq 'Storage' and armRegionName eq 'westeurope'",
        )


class TestPickItem(unittest.TestCase):
    """Choosing the right meter out of the answer."""

    def test_a_matching_meter_and_unit_are_found(self):
        items = [price_item(meter="P1 v3 App", unit="1 Hour", price=0.155)]

        found = pricing.pick_item(SAMPLE_SPEC, items)

        self.assertIsNotNone(found, "the meter should have been found")
        self.assertEqual(found["meterName"], "P1 v3 App")

    def test_the_meter_name_has_to_match(self):
        items = [price_item(meter="B1 App", unit="1 Hour", price=0.075)]

        self.assertIsNone(
            pricing.pick_item(SAMPLE_SPEC, items),
            "a different plan should not match",
        )

    def test_the_unit_has_to_match(self):
        items = [price_item(meter="P1 v3 App", unit="1 Month", price=113.15)]

        self.assertIsNone(
            pricing.pick_item(SAMPLE_SPEC, items),
            "a different unit should not match",
        )

    def test_the_cheapest_of_several_matches_wins(self):
        items = [
            price_item(meter="P1 v3 App", unit="1 Hour", price=0.315),
            price_item(meter="P1 v3 App", unit="1 Hour", price=0.155),
        ]

        found = pricing.pick_item(SAMPLE_SPEC, items)

        self.assertIsNotNone(found)
        self.assertEqual(found["retailPrice"], 0.155)

    def test_nothing_matches_so_nothing_is_returned(self):
        self.assertIsNone(pricing.pick_item(SAMPLE_SPEC, []))

    def test_the_comparison_ignores_case(self):
        items = [price_item(meter="P1 V3 APP", unit="1 HOUR", price=0.155)]

        found = pricing.pick_item(SAMPLE_SPEC, items)

        self.assertIsNotNone(found, "case should not matter")


class TestMonthlyPrice(unittest.TestCase):
    """The monthly estimate for one price item."""

    def test_the_price_is_multiplied_by_the_assumed_units(self):
        item = price_item(price=0.155)

        self.assertEqual(
            pricing.monthly_price(item, SAMPLE_SPEC),
            113.15,
        )

    def test_a_single_unit_costs_the_price_of_that_unit(self):
        spec = pricing.MeterSpec(
            component="Azure Key Vault",
            service_name="Key Vault",
            meter_matches=("operations",),
            unit_matches=("10k",),
            per_month=1.0,
            note="10,000 operations a month.",
        )

        self.assertEqual(pricing.monthly_price(price_item(price=0.03), spec), 0.03)


class TestFetchPrices(unittest.TestCase):
    """Asking the price list for the meters we need."""

    def test_one_item_per_component_is_returned(self):
        # The fake opener answers with the meters of the service that was
        # asked for, so the two components get their own price.
        meters = {
            "Azure App Service": [price_item(meter="P1 v3 App", price=0.155)],
            "Key Vault": [
                price_item(meter="Operations", unit="10K", price=0.03),
            ],
        }
        opener = FakeOpener(
            body=lambda url: {
                "Items": meters.get(_service_of(url), []),
                "NextPageLink": None,
            }
        )

        specs = (
            SAMPLE_SPEC,
            pricing.MeterSpec(
                component="Azure Key Vault",
                service_name="Key Vault",
                meter_matches=("operations",),
                unit_matches=("10k",),
                per_month=1.0,
                note="10,000 operations a month.",
            ),
        )

        found = pricing.fetch_prices(specs=specs, opener=opener)

        self.assertEqual(
            sorted(found),
            ["Azure App Service", "Azure Key Vault"],
            "both components should have a price",
        )
        self.assertEqual(found["Azure App Service"]["retailPrice"], 0.155)
        self.assertEqual(len(opener.calls), 2, "one request per component")

    def test_a_component_without_a_meter_is_left_out(self):
        opener = FakeOpener(body={"Items": [], "NextPageLink": None})

        found = pricing.fetch_prices(specs=(SAMPLE_SPEC,), opener=opener)

        self.assertEqual(found, {}, "a missing meter means no entry")

    def test_a_broken_network_raises_a_pricing_error(self):
        opener = FakeOpener(error=urllib.error.URLError("no route to host"))

        with self.assertRaises(pricing.PricingError):
            pricing.fetch_prices(specs=(SAMPLE_SPEC,), opener=opener)

    def test_an_answer_without_items_raises_a_pricing_error(self):
        opener = FakeOpener(body={"Count": 0})

        with self.assertRaises(pricing.PricingError):
            pricing.fetch_prices(specs=(SAMPLE_SPEC,), opener=opener)

    def test_an_answer_that_is_not_an_object_raises_a_pricing_error(self):
        opener = FakeOpener(body=[1, 2, 3])

        with self.assertRaises(pricing.PricingError):
            pricing.fetch_prices(specs=(SAMPLE_SPEC,), opener=opener)

    def test_the_next_page_link_is_followed(self):
        pages = [
            {"Items": [price_item(price=0.9)], "NextPageLink": "https://example.test/page2"},
            {"Items": [price_item(price=0.155)], "NextPageLink": None},
        ]
        opener = FakeOpener(body=lambda url: pages.pop(0))

        found = pricing.fetch_prices(specs=(SAMPLE_SPEC,), opener=opener)

        self.assertEqual(len(opener.calls), 2, "both pages should be read")
        self.assertEqual(found["Azure App Service"]["retailPrice"], 0.155)


class TestGetQuotes(TempCacheTestCase):
    """The quotes the report uses."""

    def setUp(self):
        super().setUp()
        self.items = {
            "Azure App Service": price_item(meter="P1 v3 App", price=0.155),
        }

    def test_a_quote_is_made_for_every_component_we_price(self):
        fetch = fake_fetcher(self.items)

        quotes = pricing.get_quotes(fetcher=fetch)

        self.assertEqual(len(quotes), len(pricing.METER_SPECS))
        app_service = quotes["Azure App Service"]
        self.assertTrue(app_service.live)
        self.assertEqual(app_service.monthly_usd, 113.15)
        self.assertEqual(app_service.meter, "P1 v3 App")
        self.assertEqual(app_service.unit, "1 Hour")

    def test_a_component_without_a_price_is_not_live(self):
        fetch = fake_fetcher(self.items)

        quotes = pricing.get_quotes(fetcher=fetch)

        self.assertFalse(
            quotes["Azure AI Search"].live,
            "a component without a meter should not be live",
        )

    def test_a_failing_fetcher_does_not_raise(self):
        fetch = fake_fetcher(error=pricing.PricingError("no network"))

        quotes = pricing.get_quotes(fetcher=fetch)

        self.assertTrue(quotes, "the components should still be reported")
        for quote in quotes.values():
            self.assertFalse(quote.live, "no component could be priced")

    def test_a_fetcher_that_explodes_does_not_raise(self):
        def boom(specs, region, timeout):
            raise RuntimeError("something else went wrong")

        quotes = pricing.get_quotes(fetcher=boom)

        self.assertTrue(all(not quote.live for quote in quotes.values()))

    def test_the_fresh_cache_is_used_instead_of_the_api(self):
        pricing.save_cache("eastus", self.items)
        fetch = fake_fetcher(
            {"Azure App Service": price_item(meter="P1 v3 App", price=0.999)}
        )

        quotes = pricing.get_quotes(fetcher=fetch)

        self.assertEqual(
            quotes["Azure App Service"].monthly_usd,
            113.15,
            "the cached price should be used",
        )
        self.assertEqual(fetch.calls, [], "the API should not be called")


class TestApplyQuotes(unittest.TestCase):
    """Putting the live prices into the stack."""

    def setUp(self):
        self.stack = [
            Component("Azure App Service", "hosting", "Managed web hosting", 30.0),
            Component("ChromaDB", "data", "Local vector store", 0.0),
        ]
        self.quotes = {
            "Azure App Service": pricing.Quote(
                component="Azure App Service",
                monthly_usd=113.15,
                unit="1 Hour",
                meter="P1 v3 App",
                note="A P1 plan.",
                live=True,
            )
        }

    def test_a_priced_component_gets_the_new_cost(self):
        new_stack, source = pricing.apply_quotes(self.stack, self.quotes)

        self.assertEqual(source, "live")
        self.assertEqual(new_stack[0].cost_month, 113.15)

    def test_an_unpriced_component_keeps_its_estimate(self):
        new_stack, _ = pricing.apply_quotes(self.stack, self.quotes)

        self.assertEqual(
            new_stack[1].cost_month,
            0.0,
            "a component without a live price keeps its rough estimate",
        )

    def test_the_originals_are_never_changed(self):
        new_stack, _ = pricing.apply_quotes(self.stack, self.quotes)

        self.assertEqual(
            self.stack[0].cost_month,
            30.0,
            "the original component must keep its rough cost",
        )
        self.assertIsNot(
            new_stack[0],
            self.stack[0],
            "the returned component should be a copy",
        )

    def test_without_any_quote_the_source_is_an_estimate(self):
        new_stack, source = pricing.apply_quotes(self.stack, {})

        self.assertEqual(source, "estimate")
        self.assertEqual(new_stack[0].cost_month, 30.0)

    def test_a_quote_that_is_not_live_is_ignored(self):
        quotes = {
            "Azure App Service": pricing.Quote(
                component="Azure App Service",
                monthly_usd=0.0,
                unit="",
                meter="",
                note="",
                live=False,
            )
        }

        new_stack, source = pricing.apply_quotes(self.stack, quotes)

        self.assertEqual(source, "estimate")
        self.assertEqual(new_stack[0].cost_month, 30.0)


class TestCache(TempCacheTestCase):
    """The on-disk cache of price items."""

    def test_a_missing_cache_is_not_a_problem(self):
        self.assertIsNone(pricing.load_cache("eastus"))

    def test_saving_and_loading_round_trip(self):
        items = {"Azure App Service": price_item(meter="P1 v3 App", price=0.155)}

        pricing.save_cache("eastus", items)
        loaded = pricing.load_cache("eastus")

        self.assertIsNotNone(loaded, "the cache should have been written")
        self.assertEqual(loaded["Azure App Service"]["retailPrice"], 0.155)

    def test_the_cache_file_has_the_expected_shape(self):
        pricing.save_cache("eastus", {"Azure App Service": price_item()})

        data = json.loads(self.cache_file.read_text(encoding="utf-8"))

        self.assertEqual(data["cache_version"], pricing.CACHE_VERSION)
        self.assertEqual(data["region"], "eastus")
        self.assertIn("fetched_at", data)
        self.assertIn("Azure App Service", data["items"])

    def test_an_old_cache_is_ignored(self):
        stale = {
            "cache_version": pricing.CACHE_VERSION,
            "region": "eastus",
            "fetched_at": (datetime.now() - timedelta(hours=48)).isoformat(
                timespec="seconds"
            ),
            "items": {"Azure App Service": price_item()},
        }
        self.cache_file.write_text(json.dumps(stale), encoding="utf-8")

        self.assertIsNone(
            pricing.load_cache("eastus", max_age_hours=24),
            "a cache older than a day should not be used",
        )

    def test_a_cache_for_another_region_is_ignored(self):
        pricing.save_cache("westeurope", {"Azure App Service": price_item()})

        self.assertIsNone(
            pricing.load_cache("eastus"),
            "the prices of one region say nothing about another",
        )

    def test_a_cache_from_another_version_is_ignored(self):
        other = {
            "cache_version": pricing.CACHE_VERSION + 1,
            "region": "eastus",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "items": {"Azure App Service": price_item()},
        }
        self.cache_file.write_text(json.dumps(other), encoding="utf-8")

        self.assertIsNone(pricing.load_cache("eastus"))

    def test_a_broken_cache_is_ignored(self):
        self.cache_file.write_text("this is not json", encoding="utf-8")

        self.assertIsNone(pricing.load_cache("eastus"))

    def test_saving_to_an_impossible_path_is_ignored(self):
        original = pricing.cache_path
        pricing.cache_path = lambda: Path(self.tmp.name) / "no" / "such" / "\0bad"
        self.addCleanup(setattr, pricing, "cache_path", original)

        pricing.save_cache("eastus", {"Azure App Service": price_item()})  # no raise


class TestMeterSpecs(unittest.TestCase):
    """The assumptions behind the prices."""

    def test_every_spec_is_filled_in(self):
        for spec in pricing.METER_SPECS:
            with self.subTest(component=spec.component):
                self.assertTrue(spec.component)
                self.assertTrue(spec.service_name)
                self.assertTrue(spec.meter_matches)
                self.assertTrue(spec.unit_matches)
                self.assertGreater(spec.per_month, 0)
                self.assertTrue(spec.note)

    def test_the_component_names_match_the_catalogue(self):
        from rules import CLOUD_COMPONENT_NAMES

        known = set(CLOUD_COMPONENT_NAMES)
        for spec in pricing.METER_SPECS:
            with self.subTest(component=spec.component):
                self.assertIn(spec.component, known)

    def test_the_units_are_written_in_lower_case(self):
        # pick_item compares the unit of an item with the unit of the spec in
        # lower case, so a spec written in capitals would never match.
        for spec in pricing.METER_SPECS:
            with self.subTest(component=spec.component):
                for unit in spec.unit_matches:
                    self.assertEqual(unit, unit.lower())


class TestTheModuleStaysSmall(unittest.TestCase):
    """The module only uses the standard library and stays quiet on import."""

    def test_no_third_party_imports(self):
        source = Path(pricing.__file__).read_text(encoding="utf-8")

        imported = set()
        for line in source.splitlines():
            line = line.strip()
            if line.startswith("import "):
                imported.add(line.split()[1].split(".")[0])
            elif line.startswith("from ") and " import " in line:
                imported.add(line.split()[1].split(".")[0])

        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:  # Python 3.9 and older
            self.skipTest("this Python has no list of standard library modules")

        allowed = set(stdlib) | {"rules"}
        self.assertTrue(
            imported <= allowed,
            f"the module should not need {sorted(imported - allowed)}",
        )

    def test_importing_uses_no_network_and_writes_no_files(self):
        # A fresh interpreter imports a COPY of the module from a temporary
        # folder, with every socket blocked.  The copy matters: the module
        # works out its cache path from where it lives, so importing the copy
        # points the cache at the temp folder instead of the repository.  That
        # keeps the test meaningful whether or not a real cache file exists -
        # a request at import time would fail the test as well.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._assert_the_import_is_clean(temp_dir)

    def _assert_the_import_is_clean(self, temp_dir):
        """Import the module in a fresh interpreter and check it did nothing."""
        temp_root = Path(temp_dir)
        (temp_root / "src").mkdir()
        shutil.copy(pricing.__file__, temp_root / "src" / "pricing.py")

        code = (
            "import socket, sys\n"
            "class Blocked(socket.socket):\n"
            "    def connect(self, *args, **kwargs):\n"
            "        raise AssertionError('the network was used at import time')\n"
            "    def connect_ex(self, *args, **kwargs):\n"
            "        raise AssertionError('the network was used at import time')\n"
            "socket.socket = Blocked\n"
            f"sys.path.insert(0, {str(temp_root / 'src')!r})\n"
            f"sys.path.insert(1, {str(SRC)!r})\n"
            "import pricing\n"
            "print(pricing.cache_path().exists())\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "False",
            "importing the module should not write the cache file",
        )


def _service_of(url):
    """The service name inside a request URL (used by the fake opener)."""
    marker = "serviceName%20eq%20%27"
    start = url.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = url.find("%27", start)
    # The service name is percent encoded inside the URL, so it has to be
    # turned back into plain text before it can be looked up.
    return urllib.parse.unquote(url[start:end])


if __name__ == "__main__":
    unittest.main()
