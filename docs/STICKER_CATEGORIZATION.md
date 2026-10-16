# Sticker categories and approval

Supported categories are `greeting`, `happy`, `celebration`, `thanks`, `thinking`, `confused`, `encouragement`, `supportive`, `programming`, `science`, `goodbye`, and `general`.

`StickerCategoryService` makes a best-effort suggestion from the associated emoji, then pack short name/title. The associated emoji describes a related emoji, not necessarily the actual image or emotion. Missing or uncertain metadata maps to `general`. Newly collected/imported entries are always pending review regardless of the suggestion.

An admin reviews `/sticker_review`, then sets a category and optional searchable tags:

```text
/sticker_categorize 42 celebration party congratulations
```

Categorizing approves the item for inline and automatic retrieval. Automatic selection uses the current message intent/category and user's sticker setting; it does not send arbitrary catalog entries simply because they exist. To change a category or tags later, run the command again with the same database ID.
