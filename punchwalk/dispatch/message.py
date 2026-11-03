"""The message a trade receives, built once and shown identically in the preview and in the send.

Copy follows DESIGN.md "Trades": a bold first line ("Unit 2B final punch: 2 electrical items"), then
per item its number, title, one line of location plus action, and a "View item and video" link to the
public share page. Safety items say so after the title. Items are listed safety first, then by number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from punchwalk.config import get_settings
from punchwalk.core.enums import SEVERITY_LABEL, TRADE_LABEL, Severity, Trade
from punchwalk.core.models import Item

SEVERITY_RANK = {Severity.safety: 0, Severity.functional: 1, Severity.cosmetic: 2}
VIEW_LINK_TEXT = "View item and video"


@dataclass
class MessageLine:
    item_id: str
    number: int
    title: str
    line: str  # "Hallway, by the hall closet. Straighten and tighten the plate."
    url: str  # "/share/item/<token>"
    severity: Severity
    image_url: str | None = None  # absolute evidence crop URL, only when the app is publicly reachable


@dataclass
class Message:
    project_name: str
    trade: Trade
    company: str
    heading: str
    lines: list[MessageLine] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return self.heading

    @property
    def item_ids(self) -> list[str]:
        return [ln.item_id for ln in self.lines]

    @property
    def text(self) -> str:
        parts = [self.heading, ""]
        for ln in self.lines:
            parts += [f"{ln.number}. {_title(ln)}", ln.line, f"{VIEW_LINK_TEXT}: {absolute(ln.url)}", ""]
        return "\n".join(parts).rstrip() + "\n"

    @property
    def slack_text(self) -> str:
        parts = [f"*{self.heading}*", ""]
        for ln in self.lines:
            parts += [_slack_item(ln), ""]
        return "\n".join(parts).rstrip() + "\n"

    def slack_blocks(self) -> list[dict[str, Any]]:
        """Block Kit: the heading, then one section per item (evidence crop as accessory when public)."""
        blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{self.heading}*"}}]
        for ln in self.lines:
            section: dict[str, Any] = {"type": "section", "text": {"type": "mrkdwn", "text": _slack_item(ln)}}
            if ln.image_url:
                section["accessory"] = {"type": "image", "image_url": ln.image_url, "alt_text": ln.title}
            blocks.append(section)
        return blocks

    def share_links(self) -> list[str]:
        return [absolute(ln.url) for ln in self.lines]


def _title(ln: MessageLine) -> str:
    return f"{ln.title} ({SEVERITY_LABEL[ln.severity].lower()})" if ln.severity == Severity.safety else ln.title


def _slack_item(ln: MessageLine) -> str:
    return f"*{ln.number}*  {_title(ln)}\n{ln.line}\n<{absolute(ln.url)}|{VIEW_LINK_TEXT}>"


def base_url() -> str:
    return get_settings().APP_BASE_URL.rstrip("/")


def absolute(path: str) -> str:
    return path if path.startswith(("http://", "https://")) else f"{base_url()}{path}"


def is_public_base() -> bool:
    """Slack can only show images it can fetch: https on a non-local host."""
    url = urlparse(base_url())
    return url.scheme == "https" and url.hostname not in (None, "localhost", "127.0.0.1", "0.0.0.0")


def heading(project_name: str, trade: Trade, n: int) -> str:
    noun = "item" if n == 1 else "items"
    return f"{project_name}: {n} {TRADE_LABEL[trade].lower()} {noun}"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def location_action_line(item: Item) -> str:
    """'Bedroom, left of the bed. Replace the cover.' (room, location detail, then the action)."""
    detail = (item.location_detail or "").strip().rstrip(".")
    room = (item.room or "").strip()
    where = f"{room}, {_lower_first(detail)}" if room and detail else room or detail[:1].upper() + detail[1:]
    action = (item.action or "").strip().rstrip(".")
    parts = [p for p in (where, action) if p]
    return ". ".join(parts) + "." if parts else item.title


def item_image_url(item: Item) -> str | None:
    if not is_public_base() or not isinstance(item.evidence, dict):
        return None
    crop = item.evidence.get("crop")
    return absolute(f"/media/{crop.lstrip('/')}") if isinstance(crop, str) and crop else None


def build_message(project_name: str, trade: Trade, company: str, items: list[Item]) -> Message:
    """Items must already have share tokens (`service.ensure_share_tokens`)."""
    ordered = sorted(items, key=lambda i: (SEVERITY_RANK.get(i.severity, 9), i.number))
    lines = [
        MessageLine(
            item_id=i.id,
            number=i.number,
            title=i.title,
            line=location_action_line(i),
            url=f"/share/item/{i.share_token}",
            severity=i.severity,
            image_url=item_image_url(i),
        )
        for i in ordered
    ]
    return Message(
        project_name=project_name,
        trade=trade,
        company=company,
        heading=heading(project_name, trade, len(lines)),
        lines=lines,
    )
