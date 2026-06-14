# Toyzz Shop Stok Takip Botu

Üç FIFA World Cup 2026 ürününü ayrı ayrı kontrol eder. Bir ürün ilk kez stokta
görüldüğünde veya stoktan düştükten sonra yeniden geldiğinde Telegram mesajı
gönderir. Ürün stokta kaldığı sürece tekrar mesaj göndermez.

Bot, Toyzz Shop sayfaları JavaScript ile oluşturulduğu için Playwright üzerinden
gerçek bir tarayıcı kullanır. Bot koruması, bağlantı hatası veya belirsiz sayfa
"stok var" kabul edilmez.

## Kurulum

Python 3.11 veya daha yeni bir sürüm kullanın:

```bash
cd /Users/yeneremirhankaya/Desktop/bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Mac'teki Google Chrome kullanılacaksa `.env` içindeki
`BROWSER_CHANNEL=chrome` satırı yeterlidir. Playwright'ın kendi Chromium
sürümünü kullanmak için bu değeri boş bırakın ve şunu çalıştırın:

```bash
playwright install chromium
```

## Telegram Ayarları

1. Telegram'da `@BotFather` ile `/newbot` komutunu çalıştırıp token alın.
2. Oluşturduğunuz bota Telegram'dan bir mesaj gönderin.
3. Tarayıcıda aşağıdaki adresi kendi token'ınızla açın:

   `https://api.telegram.org/bot<TOKEN>/getUpdates`

4. Yanıttaki `message.chat.id` değerini bulun.
5. `.env` dosyasındaki `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` alanlarını
   doldurun.

Telegram bağlantısını sınayın:

```bash
source .venv/bin/activate
python bot.py --test-telegram
```

## Çalıştırma

Bir kez kontrol:

```bash
python bot.py --once
```

Sürekli kontrol:

```bash
python bot.py
```

Durdurmak için `Ctrl+C` kullanın. Kontrol aralığı varsayılan olarak 60
saniyedir ve `.env` içindeki `CHECK_INTERVAL_SECONDS` ile değiştirilebilir.
Siteyi gereksiz yormamak için 30 saniyenin altına izin verilmez.

Ürün listesi [products.json](products.json), son kesin stok durumları ise
otomatik oluşan `state.json` dosyasındadır. Telegram bildirimi başarısız olursa
ürün stokta olarak kaydedilmez ve sonraki turda bildirim yeniden denenir.

## Test

```bash
python -m unittest discover -s tests -v
```

Bot yalnız çalıştığı sürece kontrol yapar. Mac kapalıyken de bildirim almak için
GitHub Actions kurulumu kullanılabilir.

## Ücretsiz GitHub Actions Kurulumu

Repo public olduğunda standart GitHub Actions çalıştırıcıları ücretsizdir.
`.env` dosyası repoya yüklenmez; Telegram değerleri GitHub Secrets olarak
saklanır.

1. Bu klasörü public bir GitHub reposuna gönderin.
2. Repo sayfasında `Settings > Secrets and variables > Actions` bölümünü açın.
3. `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` adında iki repository secret
   oluşturun.
4. `Actions > Stock Monitor > Run workflow` ile ilk kontrolü elle başlatın.

Sonrasında [.github/workflows/stock-monitor.yml](.github/workflows/stock-monitor.yml)
yaklaşık 5 dakikada bir çalışır. GitHub yoğunluk olduğunda zamanlanmış işleri
geciktirebilir; bu nedenle kontrol aralığı kesin süre garantisi değildir.

`state.json` yalnız stok durumu değiştiğinde otomatik commit edilir. Ayrı
`keepalive.yml` workflow'u public repolarda 60 günlük hareketsizlik nedeniyle
zamanlayıcının kapanmaması için ayda bir heartbeat commit'i oluşturur.
