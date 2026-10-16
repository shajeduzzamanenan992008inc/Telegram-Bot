# Theme performance and accessibility

The Mini App uses a local canvas rather than remote animation/video assets. Rendering pauses while the document is hidden; the canvas is capped at device-pixel ratio 1.5. `Animation` supports Auto, Full, Reduced, and Off; Auto honors `prefers-reduced-motion` and low-memory devices. The workspace also supports system, dark, and light appearance and palette choices.

Theme text and accent contrast are checked when the registry loads. Canvas decorations are `aria-hidden`, messages are announced through a polite live region, controls have labels, and the chat remains functional when visual effects are disabled. Keep decorations low contrast and nonessential. Any new scene must not hide text or block pointer input and must be safe when `requestAnimationFrame` is cancelled or the page is backgrounded.

On low-performance devices, use Auto or Reduced mode; users can disable animation from Settings. Telegram client theme changes are observed when supported by the Web App SDK. If canvas initialization or a scene is unavailable, the page's CSS palette and ordinary chat layout remain the fallback.
