# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Article overlay service: build article-to-actions mapping."""

from typing import TYPE_CHECKING, Any

from src.domain.types import ActionId, ArticleId

if TYPE_CHECKING:
    from src.domain.contract_models import ArticlesContract


def overlay(
    actions: list[Any],  # List of ActionDefinition objects (Pydantic)
    articles: "ArticlesContract",
    selected_ids: set[ActionId] | None = None,
) -> dict[ArticleId, set[ActionId]]:
    """Build mapping from articles to related action IDs.

    Constructs a bidirectional mapping between articles and actions by combining:
    - Forward mapping: Article's 'related_actions' field
    - Reverse mapping: Action's 'articles' field

    Args:
        actions: List of ActionDefinition objects (Pydantic) with 'id' and
            optional 'articles' fields.
        articles: ArticlesContract (Pydantic model) containing taxonomy with
            'id' and optional 'related_actions' fields.
        selected_ids: Optional set to filter only selected actions. If provided,
            returns only mappings for actions in this set.

    Returns:
        Dictionary mapping article IDs to sets of action IDs. Only non-empty
        mappings are included.

    Example:
        >>> actions = [
        ...     ActionDefinition(id="CTRL-9-RMS", articles=["TOPIC-9", "TOPIC-10"]),
        ... ]
        >>> articles = ArticlesContract(taxonomy=[
        ...     ArticleDefinition(id="TOPIC-9", related_actions=["CTRL-9-RMS"]),
        ... ])
        >>> result = overlay(actions, articles)
        >>> assert "TOPIC-9" in result
        >>> assert "CTRL-9-RMS" in result["TOPIC-9"]
    """
    # Start with article's explicit related_actions
    article_to_actions: dict[ArticleId, set[ActionId]] = {}

    # Process articles taxonomy (Pydantic ArticleDefinition objects)
    for article in articles.taxonomy:
        aid = article.id
        related = getattr(article, "related_actions", None)
        if isinstance(related, list):
            article_to_actions[aid] = set(related)
        else:
            article_to_actions[aid] = set()

    # Add reverse mapping from actions to articles (Pydantic ActionDefinition objects)
    for action in actions:
        act_id = action.id
        action_articles = getattr(action, "articles", []) or []

        for article_id in action_articles:
            article_to_actions.setdefault(str(article_id), set()).add(act_id)

    # Filter by selected IDs if provided
    if selected_ids is not None:
        return {
            article_id: {aid for aid in action_ids if aid in selected_ids}
            for article_id, action_ids in article_to_actions.items()
            if any(aid in selected_ids for aid in action_ids)
        }

    # Return only non-empty mappings
    return {k: v for k, v in article_to_actions.items() if v}
