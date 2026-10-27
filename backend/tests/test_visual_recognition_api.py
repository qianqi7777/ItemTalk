"""Real PG ownership, immutable private rounds, and delayed canonical model creation."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, json_value, one, transaction
from app.main import app
from app.services import products
from tests.conftest import HEADERS, register_login
from tests.test_api import post

pytestmark = pytest.mark.postgres


def ready(client):
    attempt = post(client, "/products/identify", {"inputText": "一台手机"}).json()
    candidate = {
        "candidateId": str(uuid4()),
        "brandName": "Acme",
        "modelCode": "Phone 16",
        "basis": "appearance",
        "confidence": 0.6,
        "evidence": "外观候选",
        "matchReasons": ["两枚镜头"],
        "sources": [],
    }
    with transaction() as connection:
        execute(
            connection,
            "UPDATE identification_attempts SET status='waiting_for_user',"
            "candidates=CAST(:c AS jsonb) WHERE id=:id",
            id=attempt["id"],
            c=json_value([candidate]),
        )
        execute(
            connection,
            "UPDATE jobs SET status='succeeded' WHERE payload->>'identificationId'=:id",
            id=attempt["id"],
        )
    return attempt, candidate


def test_private_candidate_confirmation_creates_model_only_after_user_selects(client):
    register_login(client)
    attempt, candidate = ready(client)
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM product_models")["n"] == 0
    response = post(
        client,
        f"/identifications/{attempt['id']}/confirm",
        {"candidateId": candidate["candidateId"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["modelCode"] == "Phone 16"
    assert response.json()["identityStatus"] == "user_confirmed"


def test_new_round_inherits_context_and_rejects_old_confirmation_and_fork(client):
    register_login(client)
    first, candidate = ready(client)
    second = post(
        client,
        "/products/identify",
        {
            "supersedesAttemptId": first["id"],
            "inputText": "补充：侧面有按键",
            "rejectedCandidateIds": [candidate["candidateId"]],
        },
    )
    assert second.status_code == 202, second.text
    value = second.json()
    assert value["roundNo"] == 2 and value["maxRounds"] == 4
    assert (
        post(
            client,
            f"/identifications/{first['id']}/confirm",
            {"candidateId": candidate["candidateId"]},
        ).status_code
        == 409
    )
    assert (
        post(
            client,
            "/products/identify",
            {"supersedesAttemptId": first["id"], "inputText": "另一次补拍"},
        ).status_code
        == 409
    )
    with transaction() as connection:
        row = one(connection, "SELECT * FROM identification_attempts WHERE id=:id", id=value["id"])
        assert "一台手机" in row["input_text"] and "侧面有按键" in row["input_text"]
        assert row["analysis"]["rejectedModels"] == ["Acme Phone 16"]


def test_other_user_cannot_refine_or_select_private_candidate(client):
    register_login(client)
    first, candidate = ready(client)
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "visual-other@example.com")
        assert (
            post(
                other,
                "/products/identify",
                {"inputText": "补拍", "supersedesAttemptId": first["id"]},
            ).status_code
            == 404
        )
        assert (
            post(
                other,
                f"/identifications/{first['id']}/confirm",
                {"candidateId": candidate["candidateId"]},
            ).status_code
            == 404
        )


def test_concurrent_refinements_create_one_successor(client):
    user = register_login(client)
    first, _ = ready(client)

    def create(note):
        from app.db import actor
        from app.errors import DomainError
        from app.schemas import IdentifyRequest

        try:
            with actor(user["id"]), transaction() as connection:
                products.identify(
                    connection,
                    IdentifyRequest(inputText=note, supersedesAttemptId=first["id"]),
                    UUID(user["id"]),
                    str(uuid4()),
                )
            return 202
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(create, ["侧面", "正面"])) == [202, 409]


def test_confirmation_and_refinement_cannot_both_win(client):
    from app.db import actor
    from app.errors import DomainError
    from app.schemas import ConfirmRequest, IdentifyRequest

    user = register_login(client)
    first, candidate = ready(client)

    def act(confirm):
        try:
            with actor(user["id"]), transaction() as connection:
                if confirm:
                    products.confirm(
                        connection,
                        UUID(first["id"]),
                        ConfirmRequest(candidateId=candidate["candidateId"]),
                        UUID(user["id"]),
                    )
                else:
                    products.identify(
                        connection,
                        IdentifyRequest(inputText="侧面有按键", supersedesAttemptId=first["id"]),
                        UUID(user["id"]),
                        str(uuid4()),
                    )
            return 200
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(act, [True, False])) == [200, 409]


def upload_photo(client, name):
    from tests.test_visual_recognition import picture

    data = picture()
    signed = post(
        client,
        "/assets/presign",
        {
            "filename": name,
            "mimeType": "image/jpeg",
            "sizeBytes": len(data),
            "kind": "product_photo",
        },
    ).json()
    assert client.put(signed["uploadUrl"], content=data).status_code == 204
    return post(client, f"/assets/{signed['assetId']}/confirm", {}).json()["id"]


def test_photo_rounds_retain_old_images_remove_selected_and_bound_rounds(client):
    from app.worker import run_once

    register_login(client)
    back = upload_photo(client, "back.jpg")
    first = post(client, "/products/identify", {"assetIds": [back]}).json()
    assert run_once()
    first = client.get(f"/api/v1/identifications/{first['id']}").json()
    assert first["observation"]["category"] == "演示设备"
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM product_models")["n"] == 0
    front = upload_photo(client, "front.jpg")
    second = post(
        client,
        "/products/identify",
        {
            "assetIds": [front],
            "supersedesAttemptId": first["id"],
            "rejectedCandidateIds": [first["candidates"][0]["candidateId"]],
        },
    ).json()
    assert [p["id"] for p in second["photos"]] == [back, front]
    assert run_once()
    second = client.get(f"/api/v1/identifications/{second['id']}").json()
    assert second["candidates"] == []
    third = post(
        client,
        "/products/identify",
        {"discardAssetIds": [back], "supersedesAttemptId": second["id"]},
    ).json()
    assert [p["id"] for p in third["photos"]] == [front]
    assert run_once()
    fourth = post(
        client,
        "/products/identify",
        {"inputText": "还有一个侧边按键", "supersedesAttemptId": third["id"]},
    ).json()
    assert run_once()
    assert (
        post(
            client, "/products/identify", {"inputText": "继续", "supersedesAttemptId": fourth["id"]}
        ).status_code
        == 422
    )
    old = client.get(f"/api/v1/identifications/{first['id']}").json()
    assert old["supersededBy"] == second["id"] and len(old["photos"]) == 1


def test_waiting_round_without_new_evidence_does_not_spend_a_round(client):
    register_login(client)
    first, _ = ready(client)
    response = post(
        client, "/products/identify", {"supersedesAttemptId": first["id"], "inputText": "一台手机"}
    )
    assert response.status_code == 422
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM identification_attempts")["n"] == 1


def test_removing_all_photos_does_not_fall_back_to_public_text_candidates(client):
    from app.worker import run_once

    user = register_login(client)
    first, candidate = ready(client)
    photo = upload_photo(client, "back.jpg")
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO identification_attempt_attachments "
            "(identification_attempt_id,asset_id,user_id,sort_order) VALUES (:i,:a,:u,0)",
            i=first["id"],
            a=photo,
            u=user["id"],
        )
        execute(
            connection,
            "UPDATE identification_attempts SET analysis=CAST(:v AS jsonb) WHERE id=:i",
            i=first["id"],
            v=json_value({"observation": {"category": "手机"}}),
        )
    second = post(
        client,
        "/products/identify",
        {
            "supersedesAttemptId": first["id"],
            "discardAssetIds": [photo],
            "rejectedCandidateIds": [candidate["candidateId"]],
        },
    ).json()
    assert second["photos"] == []
    assert run_once()
    value = client.get(f"/api/v1/identifications/{second['id']}").json()
    assert value["status"] == "waiting_for_user"
    assert value["observation"] is not None
    assert all(c.get("candidateId") and not c.get("productModelId") for c in value["candidates"])
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM product_models")["n"] == 0
        assert (
            one(connection, "SELECT max_attempts FROM jobs WHERE id=:i", i=value["jobId"])[
                "max_attempts"
            ]
            == 1
        )
