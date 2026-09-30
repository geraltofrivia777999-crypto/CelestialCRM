"""Read-only account → campaign → adset → ad tree for MetaAds v2.

Every level is aggregated from daily facts directly, never from its children: a
missing entity or a partially synchronized hierarchy must not lose spend.
Keitaro deposits are attributable to campaigns only; they are intentionally
unknown for adsets and ads rather than divided between them.
"""

from collections import defaultdict
from decimal import Decimal

from app.models import MetaAdAccount, MetaEntity, MetaStatDaily
from app.services.meta_metrics import (
    INSTALL_ACTION_TYPES,
    REGISTRATION_ACTION_TYPES,
    action_count,
)


def _facts() -> dict:
    return {"impressions": 0, "clicks": 0, "insts": 0, "regs": 0, "spend": Decimal(0)}


def build_tree(
    accounts: list[MetaAdAccount],
    entities: list[MetaEntity],
    stats: list[MetaStatDaily],
    keitaro: dict[str, dict],
    agents: dict,
) -> list[dict]:
    """`agents` — название подключения по его id: это и есть «агент» кабинета."""
    roots: dict = {}
    nodes: dict[tuple, dict] = {}
    geo_sets: dict[str, set[str]] = defaultdict(set)

    for account in accounts:
        node = {
            "id": f"account:{account.id}", "account_id": str(account.id),
            "external_id": account.external_id,
            "level": "account", "name": account.external_id,
            "account_name": account.name, "agent": agents.get(account.connection_id),
            # По агенту открывается окно подключения, из которого пришёл кабинет.
            "connection_id": str(account.connection_id) if account.connection_id else None,
            "status": (
                account.account_status or "ACTIVE"
                if account.status.value == "active" else "PAUSED"
            ),
            "currency": account.currency, "gmt": account.timezone_name,
            "budget": None, "children": [], **_facts(),
        }
        roots[account.id] = node

    def ensure(account_id, level, external_id, *, entity=None):
        if not external_id or account_id not in roots:
            return None
        key = (account_id, level, external_id)
        if key in nodes:
            node = nodes[key]
            if entity is not None:
                node["name"] = entity.name
                node["status"] = entity.effective_status
                node["budget"] = (
                    float(entity.daily_budget) if entity.daily_budget is not None else
                    float(entity.lifetime_budget) if entity.lifetime_budget is not None else None
                )
            return node
        account = roots[account_id]
        node = {
            "id": f"{account_id}:{level}:{external_id}",
            "external_id": external_id, "level": level,
            "name": entity.name if entity else external_id,
            "status": entity.effective_status if entity else None,
            "currency": account["currency"],
            "budget": (
                float(entity.daily_budget) if entity.daily_budget is not None else
                float(entity.lifetime_budget) if entity.lifetime_budget is not None else None
            ) if entity else None,
            "children": [], **_facts(),
        }
        nodes[key] = node
        return node

    # Parent links come from Meta, but daily facts can arrive before the object
    # sync. Create a named fallback node in that case instead of dropping facts.
    parent_ids = {(e.account_id, e.level, e.external_id): e.parent_external_id for e in entities}
    entity_by_key = {(e.account_id, e.level, e.external_id): e for e in entities}
    for entity in entities:
        ensure(entity.account_id, entity.level, entity.external_id, entity=entity)

    for fact in stats:
        account = roots.get(fact.account_id)
        if not account:
            continue
        path = [account]
        for level, external_id in (
            ("campaign", fact.campaign_external_id),
            ("adset", fact.adset_external_id),
            ("ad", fact.ad_external_id),
        ):
            if external_id:
                path.append(ensure(
                    fact.account_id, level, external_id,
                    entity=entity_by_key.get((fact.account_id, level, external_id)),
                ))
        geo = (fact.country_code or "").strip().upper()
        for node in path:
            node["impressions"] += fact.impressions or 0
            node["clicks"] += fact.clicks or 0
            node["insts"] += action_count(fact.actions, INSTALL_ACTION_TYPES)
            node["regs"] += action_count(fact.actions, REGISTRATION_ACTION_TYPES)
            node["spend"] += fact.spend or Decimal(0)
            if geo:
                geo_sets[node["id"]].add(geo)

    # Attach every synchronized object, even when all its metrics are zero.
    for (account_id, level, external_id), node in list(nodes.items()):
        if level == "campaign":
            parent = roots[account_id]
        else:
            parent_level = "campaign" if level == "adset" else "adset"
            parent_id = parent_ids.get((account_id, level, external_id))
            parent = nodes.get((account_id, parent_level, parent_id)) if parent_id else None
            if parent is None:
                # An orphan remains visible under its account, but its own
                # metrics are not added to the account twice.
                parent = roots[account_id]
        parent["children"].append(node)

    for (_account_id, level, external_id), node in nodes.items():
        if level != "campaign":
            node["deps"] = None
            continue
        matched = keitaro.get(external_id)
        node["deps"] = matched["sales"] if matched else None
    for account_id, account in roots.items():
        matched = [
            node["deps"] for (owner_id, level, _), node in nodes.items()
            if owner_id == account_id and level == "campaign" and node["deps"] is not None
        ]
        account["deps"] = sum(matched) if matched else None

    def finish(node):
        geos = sorted(geo_sets[node["id"]])
        node["geos"] = geos
        node["geo"] = geos[0] if len(geos) == 1 else "MULTI" if geos else None
        node["spend"] = float(node["spend"])
        node["items"] = len(node["children"])
        node["children"].sort(key=lambda item: item["name"].casefold())
        for child in node["children"]:
            finish(child)

    result = list(roots.values())
    result.sort(key=lambda item: (item["agent"] or "", item["name"]))
    for root in result:
        finish(root)
    return result
