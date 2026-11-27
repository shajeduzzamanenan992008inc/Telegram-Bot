# Theme customization

Theme definitions are editable JSON files in `src/enan_ultra/webapp/themes/`; each filename must match its `id`. The server validates definitions at startup and serves the safe visual subset to the authenticated Mini App.

Example shape:

```json
{
  "id": "robotics_club",
  "label": {"bn": "রোবোটিক্স", "en": "Robotics Club"},
  "icon": "🤖",
  "colors": {
    "background": "#101010",
    "surface": "#202020",
    "accent": "#abcdef",
    "glow": "#123456",
    "text": "#ffffff"
  },
  "animation": "neural-network",
  "motion": "low",
  "detector": {
    "keywords_en": ["robotics club", "servo motor"],
    "keywords_bn": ["সার্ভো মোটর"]
  }
}
```

Use a lowercase ID matching `^[a-z][a-z0-9_]{0,39}$`, a short icon, six-digit hex colors, and a supported canvas scene name. Text contrast must meet WCAG AA checks enforced by `ThemeRegistry`; detector lists have bounded size and term length. The existing frontend renders configured scenes with local canvas effects; adding a novel animation name alone does not create a new renderer. For a new animation, extend and test `renderScene()` in `webapp/static/app.js`, retaining a static fallback.

Restart the service after modifying bundled themes. Run `pytest tests/test_adaptive_theme.py tests/test_mini_app.py` before deployment.
