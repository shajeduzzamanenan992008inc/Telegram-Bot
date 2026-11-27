# Adaptive Living Theme Engine

The theme engine connects a lightweight topic detector to the authenticated Telegram Mini App. It does not make an extra model call: `AdaptiveThemeEngine` scores the current message and up to six recent history messages against the editable theme registry, then applies per-user mode and switching hysteresis.

## Available themes and controls

Built-in themes are `general`, `programming`, `chemistry`, `physics`, `mathematics`, `astronomy`, `biology`, `ai_robotics`, `education`, and `business`. Telegram users can use `/theme` to inspect the list, `/theme auto` to resume automatic presentation, `/theme <theme-id>` to pin a theme, `/theme space` as an astronomy alias, `/theme off` to return to the simple general theme, and `/theme reset` to clear visual preferences. `/theme tracking on|off` independently enables or pauses automatic topic detection. `/theme learning on|off` controls favorite-based fallback only.

In tracking mode, the detector reads the current message and up to six recent conversation-history messages. For private chats, it stores only the selected theme, topic ID, confidence, reason code, candidate state, and last-update timestamp in SQLite preferences under the authenticated user ID; it does not retain a second copy of message text. Group-chat and temporary-chat detections can affect that response's presentation but are not persisted to personal theme preferences. Derived topic metadata expires after 90 days; metadata created by older versions without a timestamp is cleared on the next access or maintenance run. `/theme tracking off` stops detection, clears detected topic metadata (and an automatically selected current theme), and leaves conversation history and manual appearance choices intact. `/theme tracking on` resumes from new messages; it does not backfill old history.

Theme mode, appearance, animation, palette and favorites are also stored in user-scoped SQLite preferences. Manual mode is stable until changed. Low-confidence or short messages retain the current theme; explicit topic-change wording, strong phrases, or repeated evidence can switch it. A sustained general conversation returns to the general theme. `/theme learning off` affects only favorite-based fallback and favorite changes; automatic topic detection and its 90-day metadata retention continue until `/theme tracking off`. Saved favorites remain until `/theme reset` or account deletion. Reset removes theme preferences and restores the default tracking behavior (on).

## Presentation layers

The ordinary Telegram bot can add a theme icon and label to supported rich-message headings; it cannot change Telegram's native wallpaper or client colors. The Mini App owns its own adaptive palette, canvas background, icons, chat cards and transitions. See [Mini App setup](./MINI_APP_SETUP.md), [topic detection](./TOPIC_DETECTION.md), and [theme customization](./THEME_CUSTOMIZATION.md).

## Boundaries

Topic matching is deterministic and configurable, not a semantic classifier. Roman Bangla terms cover common variants but cannot represent every spelling. Users can choose a manual theme or disable adaptation when an automatic selection is wrong. Theme choice does not affect authorization, provider routing, or message privacy.
