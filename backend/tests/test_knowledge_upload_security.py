"""Security and lifecycle checks for knowledge-base attachments."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import (
    AuditEvent,
    KnowledgeAccess,
    KnowledgeArticle,
    KnowledgeAttachment,
    KnowledgeSection,
    Permission,
    Role,
    RolePermission,
    Session,
    User,
    UserParent,
    Workspace,
)
from app.services import storage

PNG = b"\x89PNG\r\n\x1a\nsecurity-test"


def _admin_client() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return client


async def _create_knowledge_user(
    workspace_id: uuid.UUID, *, permission_codes: set[str], suffix: str
) -> dict:
    async with SessionLocal() as db:
        permissions = list(
            await db.scalars(
                select(Permission).where(Permission.code.in_(permission_codes))
            )
        )
        assert {permission.code for permission in permissions} == permission_codes
        role = Role(
            workspace_id=workspace_id,
            name=f"upload-security-role-{suffix}",
            permissions=permissions,
        )
        db.add(role)
        await db.flush()
        user = User(
            workspace_id=workspace_id,
            role_id=role.id,
            name="Knowledge security user",
            login=f"upload-security-{suffix}",
            password_hash=hash_password("upload-password"),
        )
        db.add(user)
        await db.commit()
        return {"id": user.id, "role_id": role.id, "login": user.login}


@pytest.fixture
async def upload_workspace(database, tmp_path, monkeypatch):
    """Use an isolated disk and remove only rows created by this test module."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        initial_attachment_ids = set(
            await db.scalars(
                select(KnowledgeAttachment.id).where(
                    KnowledgeAttachment.workspace_id == admin.workspace_id
                )
            )
        )
        context = {
            "workspace_id": admin.workspace_id,
            "initial_attachment_ids": initial_attachment_ids,
        }

    yield context

    async with SessionLocal() as db:
        created_attachments = list(
            await db.scalars(
                select(KnowledgeAttachment).where(
                    KnowledgeAttachment.workspace_id == context["workspace_id"],
                    KnowledgeAttachment.id.not_in(context["initial_attachment_ids"]),
                )
            )
        )
        for attachment in created_attachments:
            storage.remove(attachment.storage_path)
            await db.delete(attachment)

        test_users = list(
            await db.scalars(select(User).where(User.login.like("upload-security-%")))
        )
        test_user_ids = [user.id for user in test_users]
        test_role_ids = [user.role_id for user in test_users]
        if test_user_ids:
            await db.execute(delete(Session).where(Session.user_id.in_(test_user_ids)))
            await db.execute(delete(UserParent).where(UserParent.user_id.in_(test_user_ids)))
            await db.execute(delete(AuditEvent).where(AuditEvent.user_id.in_(test_user_ids)))
            await db.execute(delete(User).where(User.id.in_(test_user_ids)))

        sections = list(
            await db.scalars(
                select(KnowledgeSection).where(
                    KnowledgeSection.workspace_id == context["workspace_id"],
                    KnowledgeSection.title.like("upload-security-%"),
                )
            )
        )
        section_ids = [section.id for section in sections]
        test_articles = list(
            await db.scalars(
                select(KnowledgeArticle).where(
                    KnowledgeArticle.workspace_id == context["workspace_id"],
                    KnowledgeArticle.title.like("upload-security-%"),
                )
            )
        )
        for article in test_articles:
            await db.delete(article)
        await db.flush()
        if section_ids:
            await db.execute(
                delete(KnowledgeAccess).where(
                    KnowledgeAccess.section_id.in_(section_ids)
                )
            )
            await db.execute(
                delete(KnowledgeSection).where(KnowledgeSection.id.in_(section_ids))
            )
        if test_role_ids:
            await db.execute(
                delete(RolePermission).where(
                    RolePermission.role_id.in_(test_role_ids)
                )
            )
            await db.execute(delete(Role).where(Role.id.in_(test_role_ids)))
        await db.commit()


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/jpeg", b"\xff\xd8\xffanything"),
        ("image/png", PNG),
        ("image/gif", b"GIF89aanything"),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBPanything"),
        ("video/mp4", b"\x00\x00\x00\x18ftypisom"),
        ("video/webm", b"\x1aE\xdf\xa3anything"),
        ("application/pdf", b"%PDF-1.7\n"),
        ("application/zip", b"PK\x03\x04anything"),
        ("application/msword", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1anything"),
        ("text/plain", "обычный текст".encode()),
    ],
)
def test_allowed_upload_types_require_matching_signatures(
    mime_type: str, content: bytes
) -> None:
    storage.validate_content(mime_type, content)


def test_active_content_disguised_as_an_image_is_rejected(upload_workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("report.png", b"<script>alert(1)</script>", "image/png")},
        )

    assert response.status_code == 422
    assert "не соответствует" in response.json()["error"]["message"]


def test_workspace_upload_quota_is_enforced(upload_workspace, monkeypatch) -> None:
    monkeypatch.setattr(settings, "upload_workspace_quota_bytes", len(PNG) - 1)
    with _admin_client() as client:
        response = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("quota.png", PNG, "image/png")},
        )

    assert response.status_code == 413
    assert "Квота" in response.json()["error"]["message"]


def test_article_search_casefolds_cyrillic_before_applying_limit(
    upload_workspace,
) -> None:
    with _admin_client() as client:
        article = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "upload-security ИНТЕГРАЦИЯ КЕЙТАРО",
                "status": "published",
            },
        ).json()
        found = client.get(
            "/api/v1/knowledge/articles/search",
            params={"q": "интеграция кейтаро", "limit": 1},
        )
        client.delete(f"/api/v1/knowledge/articles/{article['id']}")

    assert found.status_code == 200
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["id"] == article["id"]


def test_whitespace_only_knowledge_titles_are_rejected(upload_workspace) -> None:
    with _admin_client() as client:
        assert client.post(
            "/api/v1/knowledge/sections", json={"title": "   "}
        ).status_code == 422
        assert client.post(
            "/api/v1/knowledge/articles", json={"title": " \t "}
        ).status_code == 422

        section = client.post(
            "/api/v1/knowledge/sections",
            json={"title": "upload-security-title-section"},
        ).json()
        article = client.post(
            "/api/v1/knowledge/articles",
            json={"title": "upload-security-title-article"},
        ).json()
        assert client.patch(
            f"/api/v1/knowledge/sections/{section['id']}",
            json={"title": "   "},
        ).status_code == 422
        assert client.patch(
            f"/api/v1/knowledge/articles/{article['id']}",
            json={"title": "   "},
        ).status_code == 422
        client.delete(f"/api/v1/knowledge/articles/{article['id']}")
        client.delete(f"/api/v1/knowledge/sections/{section['id']}")


def test_article_parent_must_share_section_and_cannot_create_a_cycle(
    upload_workspace,
) -> None:
    with _admin_client() as client:
        first_section = client.post(
            "/api/v1/knowledge/sections",
            json={"title": "upload-security-parent-one"},
        ).json()
        second_section = client.post(
            "/api/v1/knowledge/sections",
            json={"title": "upload-security-parent-two"},
        ).json()
        parent = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "upload-security-parent",
                "section_id": first_section["id"],
            },
        ).json()
        child = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "upload-security-child",
                "section_id": first_section["id"],
                "parent_id": parent["id"],
            },
        ).json()

        cycle = client.patch(
            f"/api/v1/knowledge/articles/{parent['id']}",
            json={"parent_id": child["id"]},
        )
        wrong_section = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "upload-security-wrong-section",
                "section_id": second_section["id"],
                "parent_id": parent["id"],
            },
        )

        assert cycle.status_code == 422
        assert wrong_section.status_code == 422
        client.delete(f"/api/v1/knowledge/articles/{child['id']}")
        client.delete(f"/api/v1/knowledge/articles/{parent['id']}")
        client.delete(f"/api/v1/knowledge/sections/{first_section['id']}")
        client.delete(f"/api/v1/knowledge/sections/{second_section['id']}")


async def test_cross_workspace_section_and_article_parents_are_hidden(
    upload_workspace,
) -> None:
    foreign_workspace_id = uuid.uuid4()
    foreign_section_id = uuid.uuid4()
    foreign_article_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            Workspace(
                id=foreign_workspace_id,
                name="upload-security-foreign-workspace",
            )
        )
        db.add(
            KnowledgeSection(
                id=foreign_section_id,
                workspace_id=foreign_workspace_id,
                title="foreign section",
            )
        )
        db.add(
            KnowledgeArticle(
                id=foreign_article_id,
                workspace_id=foreign_workspace_id,
                section_id=foreign_section_id,
                title="foreign article",
            )
        )
        await db.commit()

    try:
        with _admin_client() as client:
            own_section = client.post(
                "/api/v1/knowledge/sections",
                json={"title": "upload-security-own-section"},
            ).json()
            own_article = client.post(
                "/api/v1/knowledge/articles",
                json={
                    "title": "upload-security-own-article",
                    "section_id": own_section["id"],
                },
            ).json()

            section_move = client.patch(
                f"/api/v1/knowledge/sections/{own_section['id']}",
                json={"parent_id": str(foreign_section_id)},
            )
            article_create = client.post(
                "/api/v1/knowledge/articles",
                json={
                    "title": "upload-security-cross-parent",
                    "section_id": own_section["id"],
                    "parent_id": str(foreign_article_id),
                },
            )
            article_move = client.patch(
                f"/api/v1/knowledge/articles/{own_article['id']}",
                json={"parent_id": str(foreign_article_id)},
            )

            assert section_move.status_code == 404
            assert article_create.status_code == 404
            assert article_move.status_code == 404
            client.delete(f"/api/v1/knowledge/articles/{own_article['id']}")
            client.delete(f"/api/v1/knowledge/sections/{own_section['id']}")
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(KnowledgeArticle).where(
                    KnowledgeArticle.workspace_id == foreign_workspace_id
                )
            )
            await db.execute(
                delete(KnowledgeSection).where(
                    KnowledgeSection.workspace_id == foreign_workspace_id
                )
            )
            await db.execute(
                delete(Workspace).where(Workspace.id == foreign_workspace_id)
            )
            await db.commit()


async def test_section_editor_cannot_publish_an_article_as_loose_content(
    upload_workspace,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = await _create_knowledge_user(
        upload_workspace["workspace_id"],
        permission_codes={"knowledge.view"},
        suffix=f"editor-{suffix}",
    )
    with _admin_client() as client:
        section = client.post(
            "/api/v1/knowledge/sections",
            json={"title": f"upload-security-closed-{suffix}"},
        ).json()
        article = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": f"upload-security-closed-article-{suffix}",
                "section_id": section["id"],
                "status": "published",
            },
        ).json()

    async with SessionLocal() as db:
        db.add(
            KnowledgeAccess(
                workspace_id=upload_workspace["workspace_id"],
                section_id=uuid.UUID(section["id"]),
                role_id=user["role_id"],
                can_view=True,
                can_create=True,
                can_edit=True,
            )
        )
        await db.commit()

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"login": user["login"], "password": "upload-password"},
        )
        moved = client.patch(
            f"/api/v1/knowledge/articles/{article['id']}",
            json={"section_id": None},
        )

    assert moved.status_code == 403


async def test_knowledge_manager_bypasses_section_acl(upload_workspace) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = await _create_knowledge_user(
        upload_workspace["workspace_id"],
        permission_codes={"knowledge.view", "knowledge.manage"},
        suffix=f"manager-{suffix}",
    )
    with _admin_client() as client:
        section = client.post(
            "/api/v1/knowledge/sections",
            json={"title": f"upload-security-managed-{suffix}"},
        ).json()

    # An explicit deny still does not override the module-wide manage permission.
    async with SessionLocal() as db:
        db.add(
            KnowledgeAccess(
                workspace_id=upload_workspace["workspace_id"],
                section_id=uuid.UUID(section["id"]),
                role_id=user["role_id"],
                can_view=False,
            )
        )
        await db.commit()

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"login": user["login"], "password": "upload-password"},
        )
        tree = client.get("/api/v1/knowledge/tree")
        created = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": f"upload-security-managed-article-{suffix}",
                "section_id": section["id"],
            },
        )
        changed = client.patch(
            f"/api/v1/knowledge/articles/{created.json()['id']}",
            json={"title": f"upload-security-managed-updated-{suffix}"},
        )
        removed = client.delete(
            f"/api/v1/knowledge/articles/{created.json()['id']}"
        )

    assert tree.status_code == 200
    assert any(row["id"] == section["id"] for row in tree.json()["sections"])
    assert created.status_code == 201
    assert changed.status_code == 200
    assert removed.status_code == 200


async def test_view_only_user_needs_create_rights_for_the_upload(upload_workspace) -> None:
    suffix = uuid.uuid4().hex[:8]
    section_title = f"upload-security-section-{suffix}"
    login = f"upload-security-{suffix}"
    with _admin_client() as client:
        section = client.post(
            "/api/v1/knowledge/sections", json={"title": section_title}
        ).json()

    async with SessionLocal() as db:
        permission = await db.scalar(
            select(Permission).where(Permission.code == "knowledge.view")
        )
        role = Role(
            workspace_id=upload_workspace["workspace_id"],
            name=f"upload-security-role-{suffix}",
            permissions=[permission],
        )
        db.add(role)
        await db.flush()
        user = User(
            workspace_id=upload_workspace["workspace_id"],
            role_id=role.id,
            name="Upload security reader",
            login=login,
            password_hash=hash_password("upload-password"),
        )
        db.add(user)
        await db.commit()
        role_id = role.id

    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login",
            json={"login": login, "password": "upload-password"},
        ).status_code == 200
        without_section = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("orphan.png", PNG, "image/png")},
        )
        without_right = client.post(
            "/api/v1/knowledge/attachments",
            data={"section_id": section["id"]},
            files={"file": ("closed.png", PNG, "image/png")},
        )

    assert without_section.status_code == 403
    assert without_right.status_code == 403

    async with SessionLocal() as db:
        db.add(
            KnowledgeAccess(
                workspace_id=upload_workspace["workspace_id"],
                section_id=uuid.UUID(section["id"]),
                role_id=role_id,
                can_view=True,
                can_create=True,
            )
        )
        await db.commit()

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"login": login, "password": "upload-password"},
        )
        allowed = client.post(
            "/api/v1/knowledge/attachments",
            data={"section_id": section["id"]},
            files={"file": ("allowed.png", PNG, "image/png")},
        )

    assert allowed.status_code == 201


async def test_unbound_upload_can_be_deleted_from_database_and_disk(upload_workspace) -> None:
    with _admin_client() as client:
        uploaded = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("discard.png", PNG, "image/png")},
        ).json()

        async with SessionLocal() as db:
            attachment = await db.get(
                KnowledgeAttachment, uuid.UUID(uploaded["id"])
            )
            path = storage.resolve(attachment.storage_path)
            assert path.is_file()

        response = client.delete(
            f"/api/v1/knowledge/attachments/{uploaded['id']}"
        )

    assert response.status_code == 200
    assert not path.exists()
    async with SessionLocal() as db:
        assert await db.get(KnowledgeAttachment, uuid.UUID(uploaded["id"])) is None


async def test_deleting_an_article_also_removes_attachment_bytes(upload_workspace) -> None:
    with _admin_client() as client:
        uploaded = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("article.png", PNG, "image/png")},
        ).json()
        article = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "upload-security-article",
                "blocks": [{"type": "image", "attachment_id": uploaded["id"]}],
            },
        ).json()

        async with SessionLocal() as db:
            attachment = await db.get(
                KnowledgeAttachment, uuid.UUID(uploaded["id"])
            )
            path = storage.resolve(attachment.storage_path)

        response = client.delete(f"/api/v1/knowledge/articles/{article['id']}")

    assert response.status_code == 200
    assert not path.exists()
    async with SessionLocal() as db:
        assert await db.get(KnowledgeAttachment, uuid.UUID(uploaded["id"])) is None


async def test_stale_unbound_upload_is_reclaimed_on_the_next_upload(
    upload_workspace,
) -> None:
    old_path = storage.store(
        upload_workspace["workspace_id"], "old.png", "image/png", PNG
    )
    old_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            KnowledgeAttachment(
                id=old_id,
                workspace_id=upload_workspace["workspace_id"],
                file_name="old.png",
                mime_type="image/png",
                byte_size=len(PNG),
                storage_path=old_path,
                created_at=datetime.now(UTC)
                - timedelta(hours=settings.upload_unbound_ttl_hours + 1),
            )
        )
        await db.commit()

    with _admin_client() as client:
        response = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("new.png", PNG, "image/png")},
        )

    assert response.status_code == 201
    assert not (storage.root() / old_path).exists()
    async with SessionLocal() as db:
        assert await db.get(KnowledgeAttachment, old_id) is None


def test_a_zip_is_accepted_under_any_of_its_names() -> None:
    """Один и тот же архив браузеры называют по-разному.

    Chrome на Windows шлёт `x-zip-compressed`, часть клиентов — `octet-stream`,
    и загрузка падала с «такой тип файла загружать нельзя», хотя zip разрешён.
    """
    from app.services import storage

    archive = b"PK\x03\x04" + b"\x00" * 40
    for declared in (
        "application/zip",
        "application/x-zip-compressed",
        "application/x-zip",
        "application/octet-stream",
    ):
        storage.validate_content(declared, archive)
        assert storage.normalize_mime_type(declared, archive) == "application/zip"


def test_a_non_archive_stays_rejected_under_a_zip_name() -> None:
    """Синоним не расширяет список: формат подтверждает сигнатура, а не заголовок."""
    from app.services import storage

    executable = b"MZ\x90\x00" + b"\x00" * 40
    with pytest.raises(storage.StorageError):
        storage.validate_content("application/octet-stream", executable)
    with pytest.raises(storage.StorageError):
        storage.validate_content("application/zip", b"<html><script>alert(1)</script>")


@pytest.mark.parametrize(
    ("uploaded", "expected_ascii"),
    [
        ("AccountOpener.zip", 'filename="AccountOpener.zip"'),
        ("Отчёт за март.pdf", 'filename="file.pdf"'),
    ],
)
def test_download_keeps_the_original_file_name(
    upload_workspace, uploaded: str, expected_ascii: str
) -> None:
    """Файл должен сохраняться под своим именем, а не под id вложения.

    Имя из Content-Disposition сильнее атрибута `download` у ссылки, поэтому с
    id в заголовке браузер клал на диск «36d124b8-…» без расширения. Кириллица
    в кавычки не помещается — для неё есть filename* по RFC 5987.
    """
    content = b"PK\x03\x04zip" if uploaded.endswith(".zip") else b"%PDF-1.7\n"
    mime = "application/zip" if uploaded.endswith(".zip") else "application/pdf"
    with _admin_client() as client:
        created = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": (uploaded, content, mime)},
        )
        assert created.status_code == 201, created.text
        attachment_id = created.json()["id"]
        loaded = client.get(f"/api/v1/knowledge/attachments/{attachment_id}")

    assert loaded.status_code == 200
    disposition = loaded.headers["content-disposition"]
    assert disposition.startswith("attachment; ")
    assert expected_ascii in disposition
    assert attachment_id not in disposition
    assert disposition.endswith(f"filename*=UTF-8''{quote(uploaded, safe='')}")
