# Adaptive Living Theme Engine

The theme engine connects a lightweight topic detector to the authenticated Telegram Mini App. It does not make an extra model call: `AdaptiveThemeEngine` scores the current message and up to six recent history messages against the editable theme registry, then applies per-user mode and switching hysteresis.

## Available themes and controls

Built-in themes are `general`, `programming`, `chemistry`, `physics`, `mathematics`, `astronomy`, `biology`, `ai_robotics`, `education`, and `business`. Telegram users can use `/theme` to inspect the list, `/theme auto` to resume detection, `/theme <theme-id>` to pin a theme, `/theme space` as an astronomy alias, `/theme off` to return to the simple general theme, and `/theme reset` to clear visual preferences. `/theme learning on|off` controls favorite-based preference learning.

Theme mode, active/candidate topic, confidence, appearance, animation, palette and favorites are stored in SQLite preferences under the authenticated user ID. Manual mode is stable until changed. Low-confidence or short messages retain the current theme; explicit topic-change wording, strong phrases, or repeated evidence can switch it. A sustained general conversation returns to the general theme.

## Presentation layers

The ordinary Telegram bot can add a theme icon and label to supported rich-message headings; it cannot change Telegram's native wallpaper or client colors. The Mini App owns its own adaptive palette, canvas background, icons, chat cards and transitions. See [Mini App setup](./MINI_APP_SETUP.md), [topic detection](./TOPIC_DETECTION.md), and [theme customization](./THEME_CUSTOMIZATION.md).

## Boundaries

Topic matching is deterministic and configurable, not a semantic classifier. Roman Bangla terms cover common variants but cannot represent every spelling. Users can choose a manual theme or disable adaptation when an automatic selection is wrong. Theme choice does not affect authorization, provider routing, or message privacy.
