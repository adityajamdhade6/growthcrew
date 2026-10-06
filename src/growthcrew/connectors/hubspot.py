"""HubSpot (free tier): new leads and the deal pipeline, read-only, as a daily snapshot.

Uses a private app token with only the CRM read scopes (crm.objects.contacts.read,
crm.objects.deals.read). Contacts are counted, never copied: no names or emails are stored.
"""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.connectors import store
from growthcrew.db.models import CrmSnapshot

API = "https://api.hubapi.com/crm/v3/objects"
MAX_PAGES = 50


def _pages(client: httpx.Client, method: str, url: str, token: str, **kwargs):
    after = None
    for _ in range(MAX_PAGES):
        if method == "POST":
            body = {**kwargs["json"], **({"after": after} if after else {})}
            response = client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body)
        else:
            params = {**kwargs["params"], **({"after": after} if after else {})}
            response = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        response.raise_for_status()
        data = response.json()
        yield from data.get("results", [])
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            return


def snapshot(engine: Engine, workspace: str, client: httpx.Client, days: int = 1) -> CrmSnapshot:
    secret, _ = store.load(engine, workspace, "hubspot")
    token = secret["api_key"]
    since = datetime.now(UTC) - timedelta(days=days)
    contacts = list(
        _pages(client, "POST", f"{API}/contacts/search", token, json={
            "filterGroups": [{"filters": [{"propertyName": "createdate", "operator": "GTE",
                                           "value": str(int(since.timestamp() * 1000))}]}],
            "properties": ["createdate", "hs_analytics_source"],
            "limit": 100,
        })
    )  # fmt: skip
    deals = list(
        _pages(client, "GET", f"{API}/deals", token, params={
            "properties": "amount,hs_is_closed,hs_is_closed_won", "limit": 100,
        })
    )  # fmt: skip

    def amount(deal: dict) -> float:
        try:
            return float(deal.get("properties", {}).get("amount") or 0)
        except ValueError:
            return 0.0

    def flag(deal: dict, name: str) -> bool:
        return str(deal.get("properties", {}).get(name, "")).lower() == "true"

    open_deals = [d for d in deals if not flag(d, "hs_is_closed")]
    won = [d for d in deals if flag(d, "hs_is_closed_won")]
    sources = Counter(
        (c.get("properties", {}).get("hs_analytics_source") or "UNKNOWN").lower() for c in contacts
    )
    row = CrmSnapshot(
        workspace=workspace,
        new_contacts=len(contacts),
        open_deals=len(open_deals),
        pipeline_value=round(sum(amount(d) for d in open_deals), 2),
        won_deals=len(won),
        won_value=round(sum(amount(d) for d in won), 2),
        contacts_by_source=json.dumps(dict(sources)),
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add(row)
        session.commit()
    return row
