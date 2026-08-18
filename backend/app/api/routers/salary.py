"""Правила расчёта зарплаты — «Настройки → Расчет ЗП».

Читать правила может тот, у кого `salary.view`, менять — `salary.manage`.
Отдельно от `settings.manage` намеренно: доступ к комиссиям агентов и доступ к
чужим зарплатам — разные вещи, и в команде это обычно разные люди.

Расчёт здесь только показывает результат и ничего не записывает: начисление
делает Финансы, когда финансист открывает книгу баера. Обе стороны считают одно
и то же — правило и профит книги читаются одним кодом (`services/salary.py`).
Пока правил не завели, Финансы работают прежней лестницей.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.models import Role, SalaryComponent, SalaryRule, Status, User
from app.schemas import Page, SalaryRuleCreate, SalaryRuleUpdate
from app.services.audit import audit
from app.services.salary import (
    BASES,
    normalize_tiers,
    payroll,
)

router = APIRouter(prefix="/salary", tags=["salary"])


@router.get("/bases")
async def bases(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.view")),
) -> dict:
    """Справочник для формы правила: базы, режимы, роли и люди.

    Роли и люди отдаются здесь, а не берутся из `/roles` и `/users/options`:
    там свои права (`team.view`) и свой фильтр видимости по подчинённым, а
    правило зарплаты заводят на любого сотрудника воркспейса.
    """
    roles = list(
        (
            await db.execute(
                select(Role)
                .where(Role.workspace_id == current.workspace_id)
                .order_by(Role.name)
            )
        ).scalars()
    )
    people = list(
        (
            await db.execute(
                select(User)
                .where(
                    User.workspace_id == current.workspace_id,
                    User.status == Status.active,
                )
                .order_by(User.name, User.login)
            )
        ).scalars()
    )
    return {
        "roles": [{"id": str(role.id), "name": role.name} for role in roles],
        "users": [
            {"id": str(person.id), "name": person.name or person.login}
            for person in people
        ],
        "bases": [
            {"code": code, "label": meta["label"], "hint": meta["hint"]}
            for code, meta in BASES.items()
        ],
        "kinds": [
            {"code": "percent", "label": "Процент"},
            {"code": "fixed", "label": "Фикс"},
            {"code": "grid", "label": "Сетка"},
            {"code": "deduction", "label": "Вычет"},
        ],
        "modes": [
            {"code": "replace", "label": "Заменить предыдущие"},
            {"code": "add", "label": "Дополнить предыдущие"},
        ],
        "scopes": [
            {"code": "role", "label": "Роль"},
            {"code": "user", "label": "Пользователь"},
        ],
    }


@router.get("/rules", response_model=Page)
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.view")),
) -> Page:
    rules = await _rules(db, current.workspace_id)
    names = await _names(db, current.workspace_id)
    items = [_rule_row(rule, names) for rule in rules]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/rules", status_code=201)
async def create_rule(
    payload: SalaryRuleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.manage")),
) -> dict:
    await _validate_target(db, current, payload)
    existing = await _rules(db, current.workspace_id)
    rule = SalaryRule(
        workspace_id=current.workspace_id,
        created_by_id=current.id,
        position=len(existing),
        **payload.model_dump(exclude={"components"}),
    )
    rule.components = _components(payload)
    db.add(rule)
    await audit(
        db, current, "salary.rule_created", f"Создано правило зарплаты «{rule.name}»",
        request=request,
    )
    await db.commit()
    await db.refresh(rule)
    return _rule_row(rule, await _names(db, current.workspace_id))


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID,
    payload: SalaryRuleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.manage")),
) -> dict:
    rule = await _rule(db, current, rule_id)
    await _validate_target(db, current, payload)
    for field, value in payload.model_dump(exclude={"components"}).items():
        setattr(rule, field, value)
    # Компоненты пересобираются целиком: формулу правят как единое целое, а не
    # построчно, и сохранять половину старой было бы хуже, чем заменить всю.
    rule.components = _components(payload)
    await audit(
        db, current, "salary.rule_updated", f"Изменено правило зарплаты «{rule.name}»",
        request=request, entity_id=str(rule.id),
    )
    await db.commit()
    await db.refresh(rule)
    return _rule_row(rule, await _names(db, current.workspace_id))


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.manage")),
) -> dict:
    rule = await _rule(db, current, rule_id)
    name = rule.name
    await db.delete(rule)
    await audit(
        db, current, "salary.rule_deleted", f"Удалено правило зарплаты «{name}»",
        request=request, entity_id=str(rule_id),
    )
    await db.commit()
    return {"deleted": str(rule_id)}


@router.get("/calculate")
async def calculate(
    year: int | None = None,
    month: int | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("salary.view")),
) -> dict:
    """Что начислят каждому за месяц по действующим правилам.

    Ничего не записывает: это проверка правил, а не выплата.
    """
    today = datetime.now(UTC).date()
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный период")
    return await payroll(db, current.workspace_id, year, month)


async def _rules(db: AsyncSession, workspace_id: uuid.UUID) -> list[SalaryRule]:
    return list(
        (
            await db.execute(
                select(SalaryRule)
                .where(SalaryRule.workspace_id == workspace_id)
                .order_by(SalaryRule.position, SalaryRule.created_at)
            )
        ).scalars()
    )


async def _rule(db: AsyncSession, current: User, rule_id: uuid.UUID) -> SalaryRule:
    rule = await db.get(SalaryRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return rule


async def _names(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    roles = {
        row.id: row.name
        for row in (
            await db.execute(select(Role).where(Role.workspace_id == workspace_id))
        ).scalars()
    }
    users = {
        row.id: row.name or row.login
        for row in (
            await db.execute(select(User).where(User.workspace_id == workspace_id))
        ).scalars()
    }
    return {"roles": roles, "users": users}


async def _validate_target(db: AsyncSession, current: User, payload) -> None:
    if payload.role_id is not None:
        role = await db.get(Role, payload.role_id)
        if not role or role.workspace_id != current.workspace_id:
            raise HTTPException(status_code=422, detail="Такой роли в воркспейсе нет")
    if payload.user_id is not None:
        user = await db.get(User, payload.user_id)
        if not user or user.workspace_id != current.workspace_id:
            raise HTTPException(
                status_code=422, detail="Такого пользователя в воркспейсе нет"
            )
    for component in payload.components:
        if component.base and component.base not in BASES:
            raise HTTPException(
                status_code=422, detail=f"Неизвестная база «{component.base}»"
            )


def _components(payload) -> list[SalaryComponent]:
    return [
        SalaryComponent(
            kind=component.kind,
            base=component.base,
            percent=component.percent,
            amount=component.amount,
            tiers=normalize_tiers(
                [tier.model_dump() for tier in component.tiers]
            ),
            position=index,
        )
        for index, component in enumerate(payload.components)
    ]


def _rule_row(rule: SalaryRule, names: dict) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "status": rule.status.value,
        "mode": rule.mode,
        "scope": rule.scope,
        "role_id": str(rule.role_id) if rule.role_id else None,
        "role_name": names["roles"].get(rule.role_id),
        "user_id": str(rule.user_id) if rule.user_id else None,
        "user_name": names["users"].get(rule.user_id),
        "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
        "valid_to": rule.valid_to.isoformat() if rule.valid_to else None,
        "components": [
            {
                "kind": component.kind,
                "base": component.base,
                "base_label": BASES.get(component.base or "", {}).get("label"),
                "percent": component.percent,
                "amount": component.amount,
                "tiers": component.tiers or [],
            }
            for component in rule.components
        ],
    }
