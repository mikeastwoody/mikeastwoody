import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_profile as bp  # noqa: E402

CONFIG = {
    "country": "us",
    "apps": [
        {"id": 1, "name": "Countie", "subtitle": "Event counter widget & alerts"},
        {"id": 2, "name": "Hazlo", "subtitle": "Daily habits. Real identity."},
        {"id": 3, "name": "Tobydoro", "subtitle": "Flowmodoro deep work tracker"},
    ],
    "cards": [
        {"slug": "a", "url": "https://a.example", "label": "also building", "title": "A", "text": "One line."},
        {"slug": "b", "url": "https://b.example", "label": "open source", "title": "B", "text": "Another line."},
    ],
}


def result(app_id, version, released):
    return {
        "trackId": app_id,
        "version": version,
        "currentVersionReleaseDate": released,
        "artworkUrl512": f"https://is1-ssl.mzstatic.com/image/thumb/{app_id}/AppIcon.png/512x512bb.jpg",
    }


RESULTS = {
    1: result(1, "1.2.0", "2026-06-12T12:55:20Z"),
    2: result(2, "1.7.0", "2026-10-27T11:14:25Z"),
    3: result(3, "1.4.2", "2026-10-27T18:18:48Z"),
}


class CollectTest(unittest.TestCase):
    def test_newest_release_first_down_to_the_second(self):
        apps = bp.collect(CONFIG["apps"], RESULTS)
        self.assertEqual([app.name for app in apps], ["Tobydoro", "Hazlo", "Countie"])
        self.assertEqual(apps[0].date, "2026-10-27")

    def test_an_app_the_api_does_not_return_is_not_live(self):
        results = {k: v for k, v in RESULTS.items() if k != 2}
        self.assertEqual([app.id for app in bp.collect(CONFIG["apps"], results)], [3, 1])

    def test_an_incomplete_result_is_not_live(self):
        results = {**RESULTS, 2: dict(RESULTS[2], version="")}
        self.assertNotIn(2, [app.id for app in bp.collect(CONFIG["apps"], results)])


class LayoutTest(unittest.TestCase):
    def test_baselines_match_the_design(self):
        # Offsets measured in Chrome on Estado.dc.html.
        self.assertEqual(bp.MONO_400.baseline(13, 19.5), 14)
        self.assertEqual(bp.MONO_500.baseline(12, 18), 13)
        self.assertEqual(bp.SANS_700.baseline(44, 46.2), 39)
        self.assertEqual(bp.SANS_400.baseline(18, 27), 20)
        self.assertEqual(bp.SANS_600.baseline(14, 21), 16)

    def test_intro_wraps_where_the_design_does(self):
        self.assertEqual(
            bp.wrap(bp.INTRO, bp.SANS_400, 18, 620),
            [
                "Seven years keeping production up as an SRE / DevOps engineer. Now the",
                "production is mine: iOS apps I design, build and ship alone.",
            ],
        )

    def test_long_text_is_cut_to_fit(self):
        name = bp.fit("Teleprompter: Eye Contact for Creators and Presenters Everywhere", bp.SANS_600, 14, bp.NAME_MAX)
        self.assertTrue(name.endswith("…"))
        self.assertLessEqual(bp.SANS_600.width(name, 14), bp.NAME_MAX)

    def test_a_glyph_outside_the_subset_fails_loudly(self):
        with self.assertRaises(ValueError):
            bp.SANS_400.width("Hazlo ✓", 12)


class SvgTest(unittest.TestCase):
    def test_status_line_counts_live_apps(self):
        self.assertIn("5 of 5 apps live", bp.head_svg(5, 5))
        self.assertIn(bp.GREEN, bp.head_svg(5, 5).split("<circle", 1)[1][:60])
        self.assertIn(bp.AMBER, bp.head_svg(4, 5).split("<circle", 1)[1][:60])

    def test_every_svg_is_well_formed_and_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            icon = Path(tmp, "icon.jpg")
            icon.write_bytes(b"\xff\xd8\xff")
            app = bp.collect(CONFIG["apps"], RESULTS)[2]
            documents = [
                bp.head_svg(3, 3),
                bp.row_svg(app, icon),
                bp.foot_svg(),
                bp.card_svg(CONFIG["cards"][0], "light", 1, bp.CARD_W),
            ]
            for document in documents:
                ET.fromstring(document)
            self.assertIn("Event counter widget &amp; alerts", documents[1])


class BuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.out = root / "assets" / "profile"
        readme = root / "README.md"
        readme.write_text(
            "<!-- production:start -->\n<!-- production:end -->\n\nText.\n\n"
            "<!-- cards:start -->\n<!-- cards:end -->\n"
        )
        for name, value in {"ROOT": root, "OUT": self.out, "ICONS": self.out / "icons", "README": readme}.items():
            patcher = mock.patch.object(bp, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        fetch = mock.patch.object(bp, "fetch", return_value=b"\xff\xd8\xff")
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        self.readme = readme

    def test_a_second_run_with_the_same_data_changes_nothing(self):
        _, _, changed = bp.build(CONFIG, RESULTS)
        self.assertIn(self.readme, changed)
        _, _, changed = bp.build(CONFIG, RESULTS)
        self.assertEqual(changed, [])
        self.assertEqual(self.fetch.call_count, 3)

    def test_rows_follow_release_order_and_drop_apps_that_are_not_live(self):
        bp.build(CONFIG, RESULTS)
        readme = self.readme.read_text()
        self.assertLess(readme.index("app-3.svg"), readme.index("app-2.svg"))
        self.assertLess(readme.index("app-2.svg"), readme.index("app-1.svg"))

        apps, total, changed = bp.build(CONFIG, {k: v for k, v in RESULTS.items() if k != 2})
        self.assertEqual((len(apps), total), (2, 3))
        self.assertIn("2 of 3 apps live", (self.out / "head.svg").read_text())
        self.assertNotIn("app-2.svg", self.readme.read_text())
        self.assertFalse((self.out / "app-2.svg").exists())
        self.assertEqual(sorted(p.name.split("-")[0] for p in (self.out / "icons").glob("*.jpg")), ["1", "3"])
        self.assertIn(self.out / "app-2.svg", changed)

    def test_text_outside_the_blocks_is_kept(self):
        bp.build(CONFIG, RESULTS)
        self.assertIn("\n\nText.\n\n", self.readme.read_text())
        with self.assertRaises(SystemExit):
            self.readme.write_text("no markers")
            bp.build(CONFIG, RESULTS)


if __name__ == "__main__":
    unittest.main()
