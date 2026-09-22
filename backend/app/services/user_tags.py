"""Теги пользователя — строки, которые появляются под его офферами в Финансах.

Первый тег ставится сам: это группа офферов Keitaro. Остальные дописывают
руками. Когда оффер назначают баеру, каждый его тег становится отдельной
строкой под оффером в книге.

Группа офферов не переименовывается, поэтому смена группы не правит старый
тег, а добавляет новый под ним: депозиты, уже введённые по старому тегу,
остаются там, где их ввели.
"""

from collections.abc import Iterable

TAG_MAX_LENGTH = 120


def normalize_tags(values: Iterable[str | None]) -> list[str]:
    """Без пустых и без повторов (регистр не важен), в порядке ввода."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = (value or "").strip()[:TAG_MAX_LENGTH]
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        result.append(tag)
    return result


def tags_with_group(
    tags: Iterable[str | None], group: str | None, *, first: bool = False
) -> list[str]:
    """Теги с группой офферов Keitaro, если её среди них ещё нет.

    `first` — для нового пользователя: там группа и есть первый тег. У
    существующего новая группа встаёт в конец, под прежней.
    """
    tags = list(tags)
    return normalize_tags([group, *tags] if first else [*tags, group])
