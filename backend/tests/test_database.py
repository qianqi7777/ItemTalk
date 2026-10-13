"""提交时真实约束测试：允许纯附件，但拒绝空输入和跨用户关联。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import execute, one, transaction
from tests.conftest import register_login

pytestmark = pytest.mark.postgres


def create_attempt(connection, user, text=None):
    return one(
        connection,
        (
            "INSERT INTO identification_attempts(user_id,input_text,status) "
            "VALUES (:u,:text,'queued') RETURNING id"
        ),
        u=user,
        text=text,
    )["id"]


def create_asset(connection, user):
    return one(
        connection,
        (
            "INSERT INTO "
            "assets(owner_user_id,scope,object_key,mime_type,size_bytes,status) "
            "VALUES (:u,'private',:key,'image/jpeg',10,'available') RETURNING id"
        ),
        u=user,
        key=str(uuid4()),
    )["id"]


def attach(connection, attempt, asset, user):
    execute(
        connection,
        (
            "INSERT INTO identification_attempt_attachments "
            "(identification_attempt_id,asset_id,user_id,sort_order) VALUES "
            "(:i,:a,:u,0)"
        ),
        i=attempt,
        a=asset,
        u=user,
    )


def test_empty_insert_rejected_and_final_state_accepted(client):
    user = UUID(register_login(client)["id"])
    with pytest.raises(IntegrityError):
        with transaction() as connection:
            create_attempt(connection, user, " \n")
    with transaction() as connection:
        attempt = create_attempt(connection, user)
        execute(
            connection,
            ("UPDATE identification_attempts SET input_text='型号' WHERE id=:id"),
            id=attempt,
        )


def test_attachment_only_and_delete_last_rejected(client):
    user = UUID(register_login(client)["id"])
    with transaction() as connection:
        attempt = create_attempt(connection, user)
        asset = create_asset(connection, user)
        attach(connection, attempt, asset, user)
    with pytest.raises(IntegrityError):
        with transaction() as connection:
            execute(
                connection,
                (
                    "DELETE FROM identification_attempt_attachments WHERE "
                    "identification_attempt_id=:id"
                ),
                id=attempt,
            )
    # 父记录同事务清理时，延迟校验应跳过不存在的父记录。
    with transaction() as connection:
        execute(
            connection,
            ("DELETE FROM identification_attempt_attachments WHERE identification_attempt_id=:id"),
            id=attempt,
        )
        execute(connection, "DELETE FROM identification_attempts WHERE id=:id", id=attempt)


def test_moving_attachment_checks_old_parent(client):
    user = UUID(register_login(client)["id"])
    with transaction() as connection:
        old = create_attempt(connection, user)
        new = create_attempt(connection, user, "文字")
        asset = create_asset(connection, user)
        attach(connection, old, asset, user)
    with pytest.raises(IntegrityError):
        with transaction() as connection:
            execute(
                connection,
                (
                    "UPDATE identification_attempt_attachments SET "
                    "identification_attempt_id=:new WHERE identification_attempt_id=:old"
                ),
                new=new,
                old=old,
            )


def test_cross_user_attachment_rejected(client):
    user = UUID(register_login(client)["id"])
    with transaction() as connection:
        other = one(
            connection,
            (
                "INSERT INTO users(login,login_normalized,hashed_password) VALUES "
                "('other','other','not-a-login-hash') RETURNING id"
            ),
        )["id"]
        asset = create_asset(connection, other)
    with pytest.raises(IntegrityError):
        with transaction() as connection:
            attempt = create_attempt(connection, user)
            attach(connection, attempt, asset, user)


def test_private_asset_cannot_be_public_document(client):
    user = UUID(register_login(client)["id"])
    with transaction() as connection:
        asset = create_asset(connection, user)
        execute(connection, "UPDATE assets SET sha256=:hash WHERE id=:id", hash="0" * 64, id=asset)
        source = one(
            connection,
            (
                "INSERT INTO sources(url,normalized_url) VALUES "
                "('https://example.com','https://example.com') RETURNING id"
            ),
        )["id"]
        document = one(
            connection,
            ("INSERT INTO documents(source_id,logical_key) VALUES (:s,'manual') RETURNING id"),
            s=source,
        )["id"]
    with pytest.raises(IntegrityError):
        with transaction() as connection:
            execute(
                connection,
                (
                    "INSERT INTO "
                    "document_versions(document_id,asset_id,sha256,mime_type,fetched_at) "
                    "VALUES (:d,:a,:hash,'image/jpeg',now())"
                ),
                d=document,
                a=asset,
                hash="0" * 64,
            )
