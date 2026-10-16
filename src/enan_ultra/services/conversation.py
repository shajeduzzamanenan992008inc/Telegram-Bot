from __future__ import annotations


def bound_history(
    history: list[dict[str, str]], max_chars: int,
) -> list[dict[str, str]]:
    remaining = max(0, max_chars)
    selected: list[dict[str, str]] = []
    for item in reversed(history):
        content = item.get("content", "")
        if not isinstance(content, str) or not content or remaining <= 0:
            continue
        if len(content) > remaining:
            marker = "[Earlier context omitted]\n"
            content = (
                marker[:remaining] if remaining <= len(marker)
                else marker + content[-(remaining - len(marker)):]
            )
            selected.append({"role": item["role"], "content": content})
            break
        selected.append({"role": item["role"], "content": content})
        remaining -= len(content)
    return list(reversed(selected))
