"""Narrow input policy checks; database and evidence checks remain authoritative."""

from __future__ import annotations

import re


class PolicyViolation(ValueError):
    pass


SECRET_REQUESTS = re.compile(
    r"\b(?:show|reveal|print|return|expose|give)\b.{0,50}\b(?:api[_ -]?key|password|secret|service account|database credential|system prompt)\b",
    re.IGNORECASE,
)
AUTONOMOUS_ACTIONS = re.compile(
    r"\b(?:send|email|contact|pay|approve|place|cancel|update|delete)\b.{0,35}\b(?:automatically|without approval|on my behalf|right now)\b",
    re.IGNORECASE,
)


def check_query(query: str) -> None:
    if any(ord(character) < 32 and character not in "\n\r\t" for character in query):
        raise PolicyViolation("The query contains unsupported control characters.")
    if SECRET_REQUESTS.search(query):
        raise PolicyViolation("SKOPE cannot disclose prompts, credentials, keys, or secrets.")
    if AUTONOMOUS_ACTIONS.search(query):
        raise PolicyViolation(
            "SKOPE can recommend or draft an action, but it cannot execute external business actions autonomously."
        )

