"""Integrations: exports, and the publishers registered for workflow.publish.

A publisher is only ever called from workflow.publish, which requires an approved draft and
an explicit human confirmation.
"""

from typing import Protocol

from growthcrew.db.models import CalendarItem, Draft


class Publisher(Protocol):
    def publish(self, draft: Draft, item: CalendarItem) -> None: ...


def _publishers() -> dict[str, Publisher]:
    from growthcrew.connectors.brevo import BrevoPublisher

    # Brevo sends approved newsletters to a consented list; nothing else is sent anywhere.
    return {"brevo": BrevoPublisher()}


# name -> publisher.
PUBLISHERS: dict[str, Publisher] = _publishers()
