# TELEGRAM BOT - Resource Pack ⚡
Creator & Builder: Shajeduzzaman Enan
Ultra-animated · futuristic · thunderstorm · premium

## Inside
- 01-logo/ - primary logo, emblem, wordmark, bot avatar (animated SVG)
- 02-banners/ - GitHub 1280x640 · X header 1500x500 · link preview 1200x630 · Story 1080x1920 · BotFather 640x360 (all carry the creator credit)
- 03-backgrounds/ - thunderstorm (desktop + mobile), neon grid, transparent lightning + rain overlay
- 04-icons/ - 26 neon icons + _icon-sheet.svg
- 05-elements/ - loader, progress bar, divider, 5 status badges, thunder cloud, bolt, typing indicator, HUD frame, success + error animations
- 06-stickers/ - 8 neon stickers (SVG) + video-webm/ with 7 Telegram video stickers (WebM, 512px, 2.4 s, under 256 KB, transparent)
- 07-png-exports/ - ready-to-upload PNG: avatar, description picture, banners, logos, icons, emoji-100, stickers, creator, status, backgrounds (incl. chat wallpaper)
- 08-text-kit/ - BotFather commands + texts (EN/BN), message templates, announcements, about & credits, keyboards.json, GitHub README template, brand guide
- 09-static-svg/ - non-animated SVG versions (docs, email, print)
- 10-video/ - looping MP4: welcome animation, animated avatar, thunderstorm loop, loading / success / error, creator badge
- 11-starter-bot/ - aiogram 3 starter bot wired to the menu cards and the welcome video
- 12-creator/ - Creator & Builder badge, monogram, wide credit line, "built by" watermark
- 13-menu-cards/ - 22 header images 1280x720 (JPG + SVG): welcome, menu, profile, premium, stats, settings, language, support, help, wallet, admin, broadcast, success, error, maintenance, about, 3 plans, channel, community, referral
- 14-landing-page/ - animated landing page (index.html + assets); replace YOUR_BOT_USERNAME
- 15-telegram/ - Telegram identity: 6 "Open in Telegram" style buttons, "Available on Telegram" badge, QR frame, paper-plane hero
- preview.html - open in a browser to see the animated assets

## BotFather setup
- /setuserpic        -> 07-png-exports/bot-avatar-512.png
- /setdescriptionpic -> 07-png-exports/botfather-description-640x360.png
- /setabouttext, /setdescription -> 08-text-kit/botfather-texts-en-bn.md
- /setcommands       -> 08-text-kit/botfather-commands.txt

## Stickers and emoji
- Video stickers: @Stickers -> new video sticker pack -> upload the .webm files from 06-stickers/video-webm
- Static stickers: 07-png-exports/stickers (512x512, transparent)
- Custom emoji: 07-png-exports/emoji-100 (100x100)

## Bot messages
- Welcome: send 10-video/welcome-animation-1280x640.mp4 with sendAnimation
- Menus: send 13-menu-cards/card-*.jpg with sendPhoto + the inline keyboards in 08-text-kit/keyboards.json
- Quick start: 11-starter-bot (pip install -r requirements.txt, add BOT_TOKEN to .env, python bot.py)

## Notes
- Animated SVG plays in browsers, GitHub READMEs and websites. Telegram does not play SVG animation, so use the PNG / JPG / MP4 / WebM files there.
- Every loop is 8.5s (or a divider of it), so SVGs and videos loop without a jump.
- The wordmark is drawn from outlines: no font files needed, it looks the same everywhere.
- The paper plane is our own Telegram-style motif, not the official Telegram logo (Telegram does not allow its official logo as your own app logo). For an "Open in Telegram" style button you can use Telegram's own downloadable logos; just make clear the bot is independent.
- 11-starter-bot was syntax-checked only; it was not run against Telegram.

## কীভাবে ব্যবহার করবেন (বাংলা)
- BotFather-এ SVG আপলোড হয় না, তাই 07-png-exports ফোল্ডারের PNG ব্যবহার করুন।
- /start-এ অ্যানিমেটেড ওয়েলকাম পাঠাতে 10-video/welcome-animation-1280x640.mp4 ব্যবহার করুন।
- স্টিকার প্যাকের জন্য 06-stickers/video-webm-এর .webm ফাইল @Stickers-এ আপলোড করুন।
- ওয়েবসাইট/GitHub-এ অ্যানিমেটেড SVG সরাসরি বসানো যায়।

Creator & Builder: Shajeduzzaman Enan
