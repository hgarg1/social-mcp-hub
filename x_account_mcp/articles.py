"""Native Article payloads, separate from POST /2/tweets."""
from typing import Literal

from .errors import XError

BodyFormat = Literal["text", "markdown"]


def article_payload(title: str, body: str, body_format: BodyFormat = "markdown", cover_media_id: str | None = None) -> dict:
    if not title.strip() or not body.strip():
        raise XError("Article title and body must contain non-whitespace text.")
    if body_format == "text":
        blocks = [{"type": "unstyled", "text": paragraph} for paragraph in body.split("\n\n")]
        entities = []
    elif body_format == "markdown":
        # X's schema uses snake_case ranges and lowercase entity/style enums,
        # not the browser DraftJS camelCase representation.
        blocks = [{"type": "atomic", "text": " ", "entity_ranges": [{"key": 0, "offset": 0, "length": 1}]}]
        entities = [{"key": "0", "value": {
            "type": "markdown", "mutability": "mutable", "data": {"markdown": body},
        }}]
    else:
        raise XError("body_format must be text or markdown.")
    payload = {"title": title.strip(), "content_state": {"blocks": blocks, "entities": entities}}
    if cover_media_id is not None:
        payload["cover_media"] = {"media_id": cover_media_id, "media_category": "tweet_image"}
    return payload
