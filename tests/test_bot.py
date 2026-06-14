import json
import tempfile
import unittest
from pathlib import Path

from bot import (
    IN_STOCK,
    OUT_OF_STOCK,
    UNKNOWN,
    classify_stock,
    load_products,
    normalize_text,
    should_notify,
    update_product_state,
)


class StockClassificationTests(unittest.TestCase):
    def test_enabled_add_to_cart_control_is_in_stock(self):
        status, _ = classify_stock(
            "Ürün detayları",
            [{"text": "Sepete Ekle", "visible": True, "enabled": True}],
        )
        self.assertEqual(status, IN_STOCK)

    def test_disabled_add_to_cart_control_is_not_in_stock(self):
        status, _ = classify_stock(
            "Ürün detayları",
            [{"text": "Sepete Ekle", "visible": True, "enabled": False}],
        )
        self.assertEqual(status, UNKNOWN)

    def test_far_away_recommendation_button_is_not_in_stock(self):
        status, _ = classify_stock(
            "Ürün detayları",
            [
                {
                    "text": "Sepete Ekle",
                    "visible": True,
                    "enabled": True,
                    "near_title": False,
                }
            ],
        )
        self.assertEqual(status, UNKNOWN)

    def test_out_of_stock_text(self):
        status, _ = classify_stock("Bu ürün stokta yok.")
        self.assertEqual(status, OUT_OF_STOCK)

    def test_json_ld_in_stock(self):
        status, _ = classify_stock(
            "",
            json_ld_items=[
                {
                    "@type": "Product",
                    "offers": {"availability": "https://schema.org/InStock"},
                }
            ],
        )
        self.assertEqual(status, IN_STOCK)

    def test_json_ld_out_of_stock_wins_over_button(self):
        status, _ = classify_stock(
            "",
            [{"text": "Sepete Ekle", "visible": True, "enabled": True}],
            [{"offers": {"availability": "https://schema.org/OutOfStock"}}],
        )
        self.assertEqual(status, OUT_OF_STOCK)

    def test_conflicting_page_signals_are_unknown(self):
        status, _ = classify_stock(
            "Bu ürün stokta yok",
            [{"text": "Sepete Ekle", "visible": True, "enabled": True}],
        )
        self.assertEqual(status, UNKNOWN)

    def test_challenge_is_unknown(self):
        status, _ = classify_stock("Checking your browser before accessing")
        self.assertEqual(status, UNKNOWN)

    def test_turkish_text_normalization(self):
        self.assertEqual(normalize_text("SATIN AL"), "satin al")


class NotificationTests(unittest.TestCase):
    def test_initial_in_stock_notifies(self):
        self.assertTrue(should_notify(None, IN_STOCK))

    def test_out_to_in_notifies(self):
        self.assertTrue(should_notify(OUT_OF_STOCK, IN_STOCK))

    def test_in_to_in_does_not_notify(self):
        self.assertFalse(should_notify(IN_STOCK, IN_STOCK))

    def test_unknown_never_notifies(self):
        self.assertFalse(should_notify(OUT_OF_STOCK, UNKNOWN))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.product = type(
            "Product",
            (),
            {"product_id": "1", "name": "Ürün"},
        )()

    def test_same_status_does_not_change_state(self):
        state = {
            "products": {
                "1": {"name": "Ürün", "last_status": OUT_OF_STOCK}
            }
        }
        self.assertFalse(update_product_state(state, self.product, OUT_OF_STOCK))
        self.assertEqual(
            state["products"]["1"],
            {"name": "Ürün", "last_status": OUT_OF_STOCK},
        )

    def test_status_transition_changes_state(self):
        state = {
            "products": {
                "1": {"name": "Ürün", "last_status": OUT_OF_STOCK}
            }
        }
        self.assertTrue(update_product_state(state, self.product, IN_STOCK))
        self.assertEqual(state["products"]["1"]["last_status"], IN_STOCK)


class ProductLoadingTests(unittest.TestCase):
    def test_loads_valid_products(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "1",
                            "name": "Ürün",
                            "url": "https://example.com/product",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            products = load_products(path)
        self.assertEqual(products[0].product_id, "1")

    def test_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.json"
            product = {"id": "1", "name": "Ürün", "url": "https://example.com"}
            path.write_text(json.dumps([product, product]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Tekrarlanan"):
                load_products(path)


if __name__ == "__main__":
    unittest.main()
