import asyncio
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jarvis_recipe_recipe", ROOT / "ha" / "custom_components" / "jarvis_recipe" / "recipe.py")
rec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rec)


def page(*data, before=""):
    """A page with each of data in its own ld+json block (a str goes in as it is)."""
    blocks = "".join(f'<script type="application/ld+json">{d if isinstance(d, str) else json.dumps(d)}</script>'
                     for d in data)
    return f"<html><head>{before}{blocks}</head><body><script>alert(1)</script><p>Text</p></body></html>"


TACOS = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "WebPage", "name": "Tacos | Beispielküche"},
        {"@type": ["Recipe", "NewsArticle"], "name": "Hackfleisch-Tacos &amp; Salsa",
         "image": [{"@type": "ImageObject", "url": "/bilder/tacos.jpg"}],
         "recipeYield": ["4", "4 Portionen"],
         "totalTime": "PT1H5M",
         "recipeIngredient": ["500 g <b>Hackfleisch</b>", "8 Tortillas", "  ", 3],
         "recipeInstructions": [
             {"@type": "HowToSection", "name": "Füllung", "itemListElement": [
                 {"@type": "HowToStep", "text": "Zwiebel hacken."},
                 {"@type": "HowToStep", "text": "Hackfleisch anbraten."}]},
             {"@type": "HowToStep", "name": "Tortillas warm machen."},
             "Füllen und servieren."]},
    ],
}


class ParseTests(unittest.TestCase):
    def test_the_recipe_comes_out_of_the_graph(self):
        got = rec.parse(page(TACOS), "https://www.kueche.example.org/rezepte/tacos")
        self.assertEqual(got["title"], "Hackfleisch-Tacos & Salsa")
        self.assertEqual(got["ingredients"], ["500 g Hackfleisch", "8 Tortillas"])
        self.assertEqual(got["steps"], ["Zwiebel hacken.", "Hackfleisch anbraten.", "Tortillas warm machen.",
                                        "Füllen und servieren."])
        self.assertEqual(got["image"], "https://www.kueche.example.org/bilder/tacos.jpg")
        self.assertEqual(got["servings"], "4 Portionen")
        self.assertEqual(got["minutes"], 65)
        self.assertEqual(got["source"], "kueche.example.org")
        self.assertEqual(got["url"], "https://www.kueche.example.org/rezepte/tacos")

    def test_one_block_of_steps_is_split_into_sentences(self):
        data = {"@type": "Recipe", "name": "Suppe", "recipeIngredient": ["1 l Wasser"],
                "recipeInstructions": "Wasser kochen. Gemüse rein! 20 Minuten ziehen lassen.",
                "prepTime": "PT10M", "cookTime": "PT20M"}
        got = rec.parse(page(data), "https://example.org/suppe")
        self.assertEqual(got["steps"], ["Wasser kochen.", "Gemüse rein!", "20 Minuten ziehen lassen."])
        self.assertEqual(got["minutes"], 30)

    def test_steps_in_lines_stay_lines(self):
        data = {"@type": "Recipe", "name": "Brot", "recipeIngredient": ["Mehl"],
                "recipeInstructions": "Teig kneten. Gut kneten.\n\nBacken."}
        self.assertEqual(rec.parse(page(data), "https://example.org/b")["steps"], ["Teig kneten. Gut kneten.", "Backen."])

    def test_pages_without_a_whole_recipe_have_none(self):
        no_steps = {"@type": "Recipe", "name": "Halb", "recipeIngredient": ["Mehl"]}
        article = {"@type": "Article", "name": "Die zehn besten Tacos"}
        for p in (page(), page(article), page(no_steps), "<html>kein Rezept</html>", "", "<<<>"):
            self.assertIsNone(rec.parse(p, "https://example.org/"))

    def test_a_broken_block_does_not_hide_a_good_one(self):
        got = rec.parse(page("{nicht json", TACOS), "https://example.org/t")
        self.assertEqual(got["title"], "Hackfleisch-Tacos & Salsa")

    def test_nothing_outside_the_recipe_data_is_read(self):
        before = '<script>{"@type": "Recipe", "name": "Falle", "recipeIngredient": ["x"], "recipeInstructions": "y"}</script>'
        self.assertIsNone(rec.parse(page(before=before), "https://example.org/"))

    def test_long_lists_and_texts_are_cut(self):
        data = {"@type": "Recipe", "name": "Viel", "recipeIngredient": [f"Zutat {i}" for i in range(200)],
                "recipeInstructions": ["x" * 5000] + [f"Schritt {i}" for i in range(200)]}
        got = rec.parse(page(data), "https://example.org/")
        self.assertEqual(len(got["ingredients"]), rec.MAX_INGREDIENTS)
        self.assertEqual(len(got["steps"]), rec.MAX_STEPS)
        self.assertLessEqual(len(got["steps"][0]), rec.MAX_TEXT)

    def test_only_web_pictures(self):
        data = dict(TACOS["@graph"][1], image="javascript:alert(1)")
        self.assertEqual(rec.parse(page(data), "https://example.org/")["image"], "")

    def test_durations(self):
        for value, want in (("PT30M", 30), ("PT1H", 60), ("P1DT2H", 1560), ("pt45m", 45), ("", None),
                            ("30 min", None), ("P", None), (None, None), ("PT0M", None)):
            self.assertEqual(rec.minutes(value), want, value)


class AddressTests(unittest.TestCase):
    def test_only_plain_web_addresses_are_fetched(self):
        self.assertTrue(rec.web_address("https://example.org/rezept"))
        self.assertTrue(rec.web_address("http://example.org:8080/"))
        for bad in ("ftp://example.org/", "file:///etc/passwd", "https://user:pw@example.org/", "https://",
                    "https://example.org:99999/", "javascript:alert(1)", "example.org/rezept"):
            self.assertFalse(rec.web_address(bad), bad)

    def test_only_internet_addresses_count(self):
        self.assertTrue(rec.all_public(["8.8.8.8", "2001:4860:4860::8888"]))
        for bad in (["10.0.0.5"], ["8.8.8.8", "127.0.0.1"], ["fe80::1%eth0"], ["::1"], ["169.254.1.1"],
                    ["100.64.0.1"], [], ["nonsense"]):
            self.assertFalse(rec.all_public(bad), bad)


class LookUpTests(unittest.TestCase):
    def run_look_up(self, results, pages):
        fetched, searched = [], []

        async def search(q):
            searched.append(q)
            return results

        async def fetch(url):
            fetched.append(url)
            return pages.get(url)

        found, tried = asyncio.run(rec.look_up("Tacos", search, fetch))
        return found, tried, fetched, searched

    def test_the_first_page_with_a_recipe_wins(self):
        found, tried, fetched, searched = self.run_look_up(
            ["ftp://example.org/x", "https://www.sammlung.example.org/tacos", "https://down.example.org/",
             "https://kueche.example.org/tacos", "https://later.example.org/"],
            {"https://www.sammlung.example.org/tacos": page({"@type": "ItemList"}),
             "https://kueche.example.org/tacos": page(TACOS), "https://later.example.org/": page(TACOS)})
        self.assertEqual(searched, ["Tacos Rezept"])
        self.assertEqual(found["source"], "kueche.example.org")
        self.assertEqual(tried, ["sammlung.example.org", "down.example.org"])
        self.assertNotIn("ftp://example.org/x", fetched)
        self.assertNotIn("https://later.example.org/", fetched)

    def test_only_the_first_few_results_are_tried(self):
        urls = [f"https://s{i}.example.org/" for i in range(20)]
        found, tried, fetched, _ = self.run_look_up(urls, {})
        self.assertIsNone(found)
        self.assertEqual(len(fetched), rec.MAX_RESULTS)
        self.assertEqual(len(tried), rec.MAX_RESULTS)


class ShowTests(unittest.TestCase):
    ACTIONS = ["jarvis_show_5_recipe_show", "jarvis_show_5_dashboard_show", "kueche_recipe_show", "other_thing"]

    def test_the_prefix_is_the_shows_node_name(self):
        self.assertEqual(rec.show_prefix("Jarvis Show 5"), "jarvis_show_5")
        self.assertEqual(rec.show_prefix("  Küche--Show! "), "k_che_show")
        self.assertEqual(rec.show_prefix("genbuShow5"), "genbushow5")

    def test_the_show_that_asked_gets_it(self):
        self.assertEqual(rec.show_action(self.ACTIONS, ["Jarvis Show 5", None], True, "recipe_show"),
                         "jarvis_show_5_recipe_show")
        self.assertEqual(rec.show_action(self.ACTIONS, ["Wohnzimmer", "Kueche"], True, "recipe_show"),
                         "kueche_recipe_show")

    def test_a_device_that_is_no_show_gets_nothing(self):
        self.assertIsNone(rec.show_action(["kueche_recipe_show"], ["Pixel 9"], True, "recipe_show"))

    def test_typed_in_the_app_goes_to_the_only_show(self):
        self.assertEqual(rec.show_action(["kueche_recipe_show", "x"], [], False, "recipe_show"), "kueche_recipe_show")
        self.assertIsNone(rec.show_action(self.ACTIONS, [], False, "recipe_show"))


if __name__ == "__main__":
    unittest.main()
