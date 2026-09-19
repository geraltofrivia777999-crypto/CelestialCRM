import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    AuditEvent,
    CountryTier,
    Permission,
    ProviderType,
    Role,
    Service,
    SpendProvider,
    TaskSection,
    TaskStatus,
    User,
    Workspace,
)
from app.services.country_tiers import DEFAULT_TIER_1, TIER_1

# Пять колонок канбана из ТЗ 8.1. Заводятся системными: переименовать и
# перекрасить можно, удалить — нет, на них держится смысл доски.
TASK_STATUSES = [
    ("open", "Открыта", "#6A6161", False),
    ("in_progress", "В работе", "#C9821F", False),
    ("review", "На ревью", "#2C4E77", False),
    ("done", "Готово", "#16B57F", True),
    ("archive", "Архив", "#9B9292", True),
]

# Раздел доски по умолчанию. Разделы делят задачи по отделам, но пока отделы не
# заведены, доска обязана быть одна и работать как раньше.
DEFAULT_TASK_SECTION = "Общие задачи"

PERMISSIONS = {
    "dashboard.view",
    "media.view",
    "media.manage",
    "finance.view",
    "finance.manage",
    "finance.export",
    "offers.view",
    # Видеть весь справочник офферов, а не только назначенные лично. Право
    # отдельное от `offers.manage`: СМО смотрит за всеми командами, но офферы
    # не раздаёт, а тимлид раздаёт — но только свои.
    "offers.view_all",
    # Раздать оффер своим баерам. Отдельное от `offers.manage`: заводит и
    # правит справочник администратор, а тимлид только распределяет
    # выданное ему — создавать офферы он не должен.
    "offers.assign",
    "offers.manage",
    "team.view",
    "team.manage",
    "meta.view",
    "meta.manage",
    "meta.launch",
    # Модерация комментариев под рекламой. Право отдельное от `meta.launch`:
    # удаление комментария не тратит деньги, но необратимо и видно снаружи,
    # а чистить обычно приходится тем, кому заливы не доверены. В role_defaults
    # код не входит — существующим ролям его выдаёт админ вручную.
    "meta.comments",
    "workspace.view",
    "workspace.manage",
    # Приоритет, даты и исполнители задачи. Баерам поля не нужны — в их роли
    # права нет, дизайнеры и руководители видят и меняют их.
    "workspace.details",
    "knowledge.view",
    "knowledge.manage",
    "settings.view",
    "settings.manage",
    "salary.view",
    "salary.manage",
    "utilities.view",
    "utilities.manage",
    # Вкладки Утилит «Каналы» и «Журнал» — по отдельным правам: чаты и журнал
    # отправок видеть нужно не всем, кто настраивает свои уведомления.
    "utilities.channels",
    "utilities.events",
    # Раздел «Рекрутинг» — пока только у администратора: в role_defaults ниже
    # код не входит, а существующим ролям его выдаёт админ вручную.
    "recruitment.view",
}


def _deleted_catalog_names(
    events: list[tuple[dict | None, str | None]],
    *,
    data_key: str,
    description_prefix: str,
) -> set[str]:
    """Имена справочника, которые команда явно удалила.

    Новые события хранят имя структурированно в ``data``. Старые записи до
    этого исправления содержат его только в английском описании, поэтому они
    тоже учитываются — иначе уже удалённый BRO снова появится при деплое.
    Сравнение без учёта регистра защищает от ручной смены регистра имени.
    """
    result: set[str] = set()
    for data, description in events:
        name = str((data or {}).get(data_key) or "").strip()
        text = str(description or "").strip()
        if not name and text.startswith(description_prefix):
            name = text[len(description_prefix):].strip()
        if name:
            result.add(name.casefold())
    return result


async def seed() -> None:
    async with SessionLocal() as db:
        workspace = await db.scalar(select(Workspace).limit(1))
        if not workspace:
            workspace = Workspace(
                name="Celestial",
                timezone=settings.default_timezone,
                currency=settings.default_currency,
            )
            db.add(workspace)
            await db.flush()

        permission_rows = {}
        for code in sorted(PERMISSIONS):
            permission = await db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                permission = Permission(code=code, description=code.replace(".", " "))
                db.add(permission)
                await db.flush()
            permission_rows[code] = permission

        # Стандартные роли, которые команда удалила сама: заводить их заново при
        # каждом запуске значило бы, что удалить роль нельзя вовсе.
        deleted_roles = {
            str((data or {}).get("role_name") or "")
            for data in (
                await db.scalars(
                    select(AuditEvent.data).where(
                        AuditEvent.workspace_id == workspace.id,
                        AuditEvent.event_type == "role.deleted",
                    )
                )
            )
        }
        admin_exists = await db.scalar(
            select(User.id).where(
                User.workspace_id == workspace.id,
                User.login == settings.admin_login.lower(),
            )
        )
        admin_role = await db.scalar(
            select(Role).where(Role.workspace_id == workspace.id, Role.name == "Administrator")
        )
        if not admin_role and "Administrator" in deleted_roles and admin_exists:
            # Роль администратора удалили, а сам администратор уже переехал на
            # другую роль — ему нечего дозаводить.
            pass
        elif not admin_role:
            admin_role = Role(
                workspace_id=workspace.id,
                name="Administrator",
                description="Full access",
                is_system=True,
                data_scope="all",
                permissions=list(permission_rows.values()),
            )
            db.add(admin_role)
            await db.flush()
        else:
            # Роль живёт давно: досыпаем права, появившиеся после её создания
            # (иначе новый раздел остаётся у роли невидимым до правки руками).
            existing = {permission.code for permission in admin_role.permissions}
            missing = [
                permission for code, permission in permission_rows.items()
                if code not in existing
            ]
            if missing:
                admin_role.permissions = list(admin_role.permissions) + missing
                await db.flush()

        role_defaults = {
            "Team Lead": {
                "dashboard.view",
                "media.view",
                "media.manage",
                "finance.view",
                "offers.view",
                "offers.assign",
                "team.view",
                "meta.view",
                "meta.launch",
                "workspace.view",
                "workspace.manage",
                "workspace.details",
                "knowledge.view",
                "knowledge.manage",
                "settings.view",
                "salary.view",
                "utilities.view",
                "utilities.channels",
                "utilities.events",
                "utilities.manage",
            },
            "Buyer": {
                "dashboard.view",
                "media.view",
                "media.manage",
                "offers.view",
                "meta.view",
                "workspace.view",
                "knowledge.view",
                "utilities.view",
                "utilities.channels",
                "utilities.events",
            },
            "CMO": {
                "dashboard.view",
                "finance.view",
                "offers.view",
                "offers.view_all",
                "team.view",
                "workspace.view",
                "knowledge.view",
                "settings.view",
                "salary.view",
                "utilities.view",
                "utilities.channels",
                "utilities.events",
            },
            "Finance": {
                "dashboard.view",
                "finance.view",
                "finance.manage",
                "finance.export",
                "offers.view",
                "workspace.view",
                "knowledge.view",
                # Экран настроек нужен, чтобы дойти до раздела «Расчет ЗП».
                "settings.view",
                "salary.view",
                "salary.manage",
                "utilities.view",
                "utilities.channels",
                "utilities.events",
            },
        }
        for name, codes in role_defaults.items():
            if name in deleted_roles:
                continue
            role = await db.scalar(
                select(Role).where(Role.workspace_id == workspace.id, Role.name == name)
            )
            if not role:
                db.add(
                    Role(
                        workspace_id=workspace.id,
                        name=name,
                        is_system=True,
                        permissions=[permission_rows[code] for code in codes],
                    )
                )

        admin = await db.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.login == settings.admin_login.lower(),
            )
        )
        if not admin:
            db.add(
                User(
                    workspace_id=workspace.id,
                    role_id=admin_role.id,
                    name="Administrator",
                    login=settings.admin_login.lower(),
                    password_hash=hash_password(settings.admin_password),
                )
            )

        for position, (code, title, color, terminal) in enumerate(TASK_STATUSES):
            existing = await db.scalar(
                select(TaskStatus).where(
                    TaskStatus.workspace_id == workspace.id, TaskStatus.code == code
                )
            )
            if not existing:
                db.add(
                    TaskStatus(
                        workspace_id=workspace.id,
                        code=code,
                        name=title,
                        color=color,
                        position=position,
                        is_system=True,
                        is_terminal=terminal,
                    )
                )

        # Раздел заводится, только если на доске нет ни одного: команда могла
        # переименовать его под себя или завести свои отделы, и второй «Общие
        # задачи» после каждого запуска ей не нужен.
        has_section = await db.scalar(
            select(TaskSection.id).where(TaskSection.workspace_id == workspace.id).limit(1)
        )
        if not has_section:
            db.add(TaskSection(workspace_id=workspace.id, title=DEFAULT_TASK_SECTION))

        # Tier1 заводится один раз и дальше правится в настройках: seed не
        # переписывает уже принятые командой решения, а только доносит базу
        # в воркспейс, где справочника ещё нет.
        known_tiers = set(
            (
                await db.scalars(
                    select(CountryTier.code).where(
                        CountryTier.workspace_id == workspace.id
                    )
                )
            ).all()
        )
        if not known_tiers:
            for code in DEFAULT_TIER_1:
                db.add(
                    CountryTier(workspace_id=workspace.id, code=code, tier=TIER_1)
                )

        deleted_services = _deleted_catalog_names(
            list(
                (
                    await db.execute(
                        select(AuditEvent.data, AuditEvent.description).where(
                            AuditEvent.workspace_id == workspace.id,
                            AuditEvent.event_type == "service.deleted",
                        )
                    )
                ).all()
            ),
            data_key="service_name",
            description_prefix="Deleted service ",
        )
        for name, cost, commission in [
            ("PWA", "0.0300", "0"),
            ("SKAK", "0.0250", "2"),
            ("ZM", "0.0180", "1.5"),
            ("HEROES", "0.0400", "3"),
        ]:
            existing = await db.scalar(
                select(Service).where(Service.workspace_id == workspace.id, Service.name == name)
            )
            if not existing and name.casefold() not in deleted_services:
                db.add(
                    Service(
                        workspace_id=workspace.id,
                        name=name,
                        install_cost=Decimal(cost),
                        commission_pct=Decimal(commission),
                    )
                )
        deleted_providers = _deleted_catalog_names(
            list(
                (
                    await db.execute(
                        select(AuditEvent.data, AuditEvent.description).where(
                            AuditEvent.workspace_id == workspace.id,
                            AuditEvent.event_type == "spend_provider.deleted",
                        )
                    )
                ).all()
            ),
            data_key="provider_name",
            description_prefix="Deleted provider ",
        )
        for name, provider_type, commission in [
            ("BRO", ProviderType.agent, "8.5"),
            ("MT", ProviderType.agent, "7"),
            ("SPX", ProviderType.payment, "4.5"),
        ]:
            existing = await db.scalar(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == workspace.id,
                    SpendProvider.name == name,
                )
            )
            if not existing and name.casefold() not in deleted_providers:
                db.add(
                    SpendProvider(
                        workspace_id=workspace.id,
                        name=name,
                        provider_type=provider_type,
                        commission_pct=Decimal(commission),
                    )
                )
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
