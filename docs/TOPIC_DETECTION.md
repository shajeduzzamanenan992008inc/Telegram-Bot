# Topic detection

`TopicDetector` in `src/enan_ultra/services/theme_engine.py` scores the current message and a bounded slice of recent history against each JSON theme's `detector.keywords_en` and `detector.keywords_bn`. The `keywords_en` list may also contain Roman Bangla. Current-message matches carry more weight than prior-message matches. A single isolated word is deliberately capped as low confidence.

The configured vocabulary includes English, Bengali-script, mixed-language, and selected Roman Bangla inputs, for example:

| Topic | Example input |
| --- | --- |
| Programming | `ami python programming sikhte chai` |
| Chemistry | `ekhon chemistry te atom bujhao` |
| Astronomy | `black hole niye bolo` |

The engine preserves selected topic context through follow-up messages. It does not rewrite user text, use a spelling-correction service, or claim to infer every typo. Topic IDs, confidence, match evidence, and reason codes are returned as a structured `TopicDetection`.

## Switching behavior

Automatic mode retains the current theme for uncertain input. A sufficiently strong multi-match or specific phrase can switch immediately; explicit English, Bengali, or common Roman Bangla topic-change cues can also switch promptly. Otherwise, repeated candidate evidence is required. The Mini App sends each prompt to the same resolver before streaming the reply, and the `theme` event updates the client workspace.

## Custom topics

Add a JSON definition under `src/enan_ultra/webapp/themes/` with a safe filename matching `id`, a Bengali or English label, icon, five validated colors, animation name, and detector lists. Add Romanized spellings as `keywords_en`. The registry validates IDs, colors, keyword lengths/counts, and WCAG text/accent contrast at startup; invalid theme files are logged and ignored. Keep generic single-word terms conservative to avoid ambiguous matches.

This is keyword/context matching with hysteresis, not a model-backed intent router; validate new vocabulary against realistic short and follow-up messages; the current suite has no dedicated topic-detection test.
