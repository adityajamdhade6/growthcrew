"""Integrations. Export only for now; real publishers register here later.

A publisher is only ever called from workflow.publish, which requires an approved draft and
an explicit human confirmation.
"""

from typing import Protocol

from growthcrew.db.models import CalendarItem, Draft


class Publisher(Protocol):
    def publish(self, draft: Draft, item: CalendarItem) -> None: ...


# name -> publisher. Empty until a real integration (LinkedIn, a newsletter tool) is added.
PUBLISHERS: dict[str, Publisher] = {}
