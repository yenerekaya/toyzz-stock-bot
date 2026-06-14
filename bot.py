#!/usr/bin/env python3
"""Toyzz Shop stock monitor with Telegram notifications."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PRODUCTS_FILE = BASE_DIR / "products.json"
DEFAULT_STATE_FILE = BASE_DIR / "state.json"

IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"
UNKNOWN = "unknown"

POSITIVE_TEXTS = ("sepete ekle", "hemen al", "satin al")
NEGATIVE_TEXTS = (
    "stokta yok",
    "stokta bulunmamaktadir",
    "tukendi",
    "gelince haber ver",
    "stok alarmi",
    "temin edilemiyor",
    "satis disi",
)
CHALLENGE_TEXTS = (
    "access denied",
    "cf-chl-",
    "checking your browser",
    "cloudflare ray id",
    "captcha",
    "verify you are human",
    "robot olmadiginizi",
)


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    url: str


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    check_interval_seconds: int
    page_timeout_seconds: int
    page_settle_seconds: int
    headless: bool
    browser_channel: str | None
    products_file: Path
    state_file: Path


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


def _availability_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() == "availability" and isinstance(child, str):
                values.append(normalize_text(child))
            values.extend(_availability_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_availability_values(child))
    return values


def classify_stock(
    body_text: str,
    controls: list[dict[str, Any]] | None = None,
    json_ld_items: list[Any] | None = None,
) -> tuple[str, str]:
    """Classify a rendered product page without turning ambiguity into stock."""
    normalized_body = normalize_text(body_text)

    if any(marker in normalized_body for marker in CHALLENGE_TEXTS):
        return UNKNOWN, "Bot koruması veya doğrulama sayfası görüldü"

    availability_values: list[str] = []
    for item in json_ld_items or []:
        availability_values.extend(_availability_values(item))

    structured_out = any(
        "outofstock" in value or "soldout" in value
        for value in availability_values
    )
    structured_in = any(
        "instock" in value or "limitedavailability" in value
        for value in availability_values
    )
    if structured_out and structured_in:
        return UNKNOWN, "Yapılandırılmış stok verileri çelişkili"
    if structured_out:
        return OUT_OF_STOCK, "Yapılandırılmış veride OutOfStock"
    if structured_in:
        return IN_STOCK, "Yapılandırılmış veride InStock"

    positive_control = ""
    for control in controls or []:
        text = normalize_text(str(control.get("text", "")))
        enabled = bool(control.get("enabled"))
        visible = bool(control.get("visible"))
        near_title = bool(control.get("near_title", True))
        if (
            visible
            and enabled
            and near_title
            and any(marker in text for marker in POSITIVE_TEXTS)
        ):
            positive_control = text
            break

    has_negative_text = any(marker in normalized_body for marker in NEGATIVE_TEXTS)
    if positive_control and has_negative_text:
        return UNKNOWN, "Satın alma ve stok yok sinyalleri çelişkili"
    if has_negative_text:
        return OUT_OF_STOCK, "Sayfada stok yok sinyali"
    if positive_control:
        return IN_STOCK, f"Etkin satın alma kontrolü: {positive_control!r}"

    return UNKNOWN, "Kesin stok sinyali bulunamadı"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} true/false olmalı")


def env_int(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} tam sayı olmalı") from exc
    if value < minimum:
        raise ValueError(f"{name} en az {minimum} olmalı")
    return value


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")
    channel = os.getenv("BROWSER_CHANNEL", "").strip() or None
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        check_interval_seconds=env_int("CHECK_INTERVAL_SECONDS", 60, 30),
        page_timeout_seconds=env_int("PAGE_TIMEOUT_SECONDS", 30, 5),
        page_settle_seconds=env_int("PAGE_SETTLE_SECONDS", 12, 1),
        headless=env_bool("HEADLESS", True),
        browser_channel=channel,
        products_file=Path(
            os.getenv("PRODUCTS_FILE", str(DEFAULT_PRODUCTS_FILE))
        ).expanduser(),
        state_file=Path(os.getenv("STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser(),
    )


def load_products(path: Path) -> list[Product]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Ürün dosyası bulunamadı: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ürün dosyası geçerli JSON değil: {path}") from exc

    if not isinstance(data, list) or not data:
        raise ValueError("Ürün dosyası boş olmayan bir JSON listesi olmalı")

    products: list[Product] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{index}. ürün nesne olmalı")
        product_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not product_id or not name or not url.startswith(("https://", "http://")):
            raise ValueError(f"{index}. üründe id, name veya geçerli url eksik")
        if product_id in seen_ids:
            raise ValueError(f"Tekrarlanan ürün id: {product_id}")
        seen_ids.add(product_id)
        products.append(Product(product_id, name, url))
    return products


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"products": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Durum dosyası okunamadı; boş durumla devam ediliyor")
        return {"products": {}}
    if not isinstance(data, dict) or not isinstance(data.get("products"), dict):
        logging.error("Durum dosyası biçimi geçersiz; boş durumla devam ediliyor")
        return {"products": {}}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def should_notify(previous_status: str | None, current_status: str) -> bool:
    return current_status == IN_STOCK and previous_status != IN_STOCK


def update_product_state(
    state: dict[str, Any], product: Product, status: str
) -> bool:
    product_states = state.setdefault("products", {})
    previous = product_states.get(product.product_id, {})
    if previous.get("last_status") == status:
        return False
    product_states[product.product_id] = {
        "name": product.name,
        "last_status": status,
    }
    return True


def _telegram_request(token: str, payload: dict[str, str]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram isteği başarısız: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API hatası: {result}")


async def send_telegram(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ayarlanmalı")
    await asyncio.to_thread(
        _telegram_request,
        settings.telegram_bot_token,
        {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "disable_web_page_preview": "false",
        },
    )


async def read_rendered_page(
    page: Any, product: Product
) -> tuple[str, list[dict[str, Any]], list[Any]]:
    snapshot = await page.evaluate(
        """
        ({expectedName, productId}) => {
          const normalize = (value) => (value || '')
            .toLocaleLowerCase('tr-TR')
            .normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
          const expected = normalize(expectedName);
          const purchaseMarkers = ['sepete ekle', 'hemen al', 'satin al'];
          const unavailableMarkers = [
            'stokta yok',
            'stokta bulunmamaktadir',
            'tukendi',
            'gelince haber ver',
            'stok alarmi',
            'temin edilemiyor',
            'satis disi'
          ];
          const hasStockMarker = (element) => {
            const text = normalize(element.innerText);
            return purchaseMarkers.some((marker) => text.includes(marker))
              || unavailableMarkers.some((marker) => text.includes(marker));
          };
          const title = Array.from(
            document.querySelectorAll('h1, [itemprop="name"]')
          ).find((element) => {
            const text = normalize(element.textContent);
            return text && (text.includes(expected) || expected.includes(text));
          });

          let productRoot = null;
          let candidate = title;
          for (let depth = 0; candidate && depth < 8; depth += 1) {
            if (hasStockMarker(candidate)) {
              productRoot = candidate;
              break;
            }
            candidate = candidate.parentElement;
          }

          const controlRoot = productRoot || document.createElement('div');
          const controls = Array.from(
            controlRoot.querySelectorAll(
              'button, [role="button"], input[type="submit"], a'
            )
          ).map((element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const visible = style.display !== 'none'
              && style.visibility !== 'hidden'
              && rect.width > 0
              && rect.height > 0;
            const disabled = element.disabled
              || element.getAttribute('aria-disabled') === 'true'
              || element.classList.contains('disabled');
            const text = element.innerText
              || element.value
              || element.getAttribute('aria-label')
              || element.getAttribute('title')
              || '';
            const titleRect = title ? title.getBoundingClientRect() : null;
            const nearTitle = titleRect
              ? Math.abs(rect.top - titleRect.top) <= 900
              : false;
            return {text, visible, enabled: !disabled, near_title: nearTitle};
          });

          const parsedJsonLd = Array.from(
            document.querySelectorAll('script[type="application/ld+json"]')
          ).map((script) => {
            try { return JSON.parse(script.textContent); }
            catch (_) { return null; }
          }).filter(Boolean);
          const productSchemas = [];
          const collectProducts = (value) => {
            if (Array.isArray(value)) {
              value.forEach(collectProducts);
              return;
            }
            if (!value || typeof value !== 'object') return;
            const types = Array.isArray(value['@type'])
              ? value['@type']
              : [value['@type']];
            if (types.some((type) => normalize(type) === 'product')) {
              productSchemas.push(value);
            }
            Object.values(value).forEach(collectProducts);
          };
          parsedJsonLd.forEach(collectProducts);
          const jsonLd = productSchemas.filter((schema) => {
            const schemaName = normalize(schema.name);
            const identifiers = [
              schema.sku,
              schema.productID,
              schema.mpn
            ].filter(Boolean).map(String);
            const nameMatches = schemaName
              && (schemaName.includes(expected) || expected.includes(schemaName));
            const idMatches = identifiers.some((value) => value.includes(productId));
            return nameMatches || idMatches;
          });

          return {
            bodyText: productRoot
              ? productRoot.innerText
              : (document.body ? document.body.innerText : ''),
            controls,
            jsonLd
          };
        }
        """,
        {"expectedName": product.name, "productId": product.product_id},
    )
    return snapshot["bodyText"], snapshot["controls"], snapshot["jsonLd"]


async def check_product(
    page: Any, product: Product, settings: Settings
) -> tuple[str, str]:
    logging.info("Kontrol ediliyor: %s", product.name)
    await page.goto(
        product.url,
        wait_until="domcontentloaded",
        timeout=settings.page_timeout_seconds * 1000,
    )

    deadline = asyncio.get_running_loop().time() + settings.page_settle_seconds
    last_result = (UNKNOWN, "Sayfa henüz hazır değil")
    while True:
        body_text, controls, json_ld_items = await read_rendered_page(page, product)
        last_result = classify_stock(body_text, controls, json_ld_items)
        if last_result[0] != UNKNOWN:
            return last_result
        if asyncio.get_running_loop().time() >= deadline:
            return last_result
        await asyncio.sleep(1)


async def run_check_cycle(
    browser: Any,
    products: list[Product],
    settings: Settings,
    state: dict[str, Any],
) -> None:
    context = await browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul")
    try:
        for product in products:
            page = await context.new_page()
            try:
                status, reason = await check_product(page, product, settings)
            except Exception as exc:
                status, reason = UNKNOWN, f"{type(exc).__name__}: {exc}"
                logging.error("%s kontrol edilemedi: %s", product.name, reason)
            finally:
                await page.close()

            product_states = state.setdefault("products", {})
            previous = product_states.get(product.product_id, {})
            previous_status = previous.get("last_status")

            if status == UNKNOWN:
                logging.warning("%s: BELİRSİZ (%s)", product.name, reason)
                continue

            logging.info("%s: %s (%s)", product.name, status, reason)
            if should_notify(previous_status, status):
                message = (
                    "STOK GELDİ!\n\n"
                    f"Ürün: {product.name}\n"
                    f"Link: {product.url}"
                )
                try:
                    await send_telegram(settings, message)
                except Exception:
                    logging.exception(
                        "%s için Telegram bildirimi gönderilemedi; tekrar denenecek",
                        product.name,
                    )
                    continue
                logging.info("%s için Telegram bildirimi gönderildi", product.name)

            if update_product_state(state, product, status):
                save_state(settings.state_file, state)
    finally:
        await context.close()


async def run_monitor(settings: Settings, once: bool) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright kurulu değil. Önce: pip install -r requirements.txt"
        ) from exc

    products = load_products(settings.products_file)
    state = load_state(settings.state_file)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, stop_event.set)
        except NotImplementedError:
            pass

    async with async_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": settings.headless}
        if settings.browser_channel:
            launch_options["channel"] = settings.browser_channel
        browser = await playwright.chromium.launch(**launch_options)
        try:
            while not stop_event.is_set():
                await run_check_cycle(browser, products, settings, state)
                if once:
                    break
                logging.info(
                    "Sonraki kontrol %s saniye sonra", settings.check_interval_seconds
                )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=settings.check_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Toyzz Shop stok takip botu")
    parser.add_argument(
        "--once", action="store_true", help="Ürünleri bir kez kontrol edip çık"
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Telegram ayarlarını test eden bir mesaj gönderip çık",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    settings = load_settings()
    if args.test_telegram:
        await send_telegram(settings, "Toyzz Shop stok botu Telegram testi başarılı.")
        logging.info("Telegram test mesajı gönderildi")
        return
    await run_monitor(settings, args.once)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, ValueError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
