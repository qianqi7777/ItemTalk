"""Reviewed metadata repairs an unregistered manufacturer without undoing revocation."""

import pytest

from app.db import execute, one, transaction
from app.services.products import product_model

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("brand", ["ROSESELSA", "弱水时砂"])
def test_confirming_reviewed_manufacturer_registers_domains(database, brand):
    with transaction() as connection:
        model = product_model(connection, brand, "CERAMICS Ultra")
        row = one(
            connection, "SELECT official_domains FROM brands WHERE id=:id", id=model["brand_id"]
        )
        assert "roseselsa.com" in row["official_domains"]


def test_existing_null_registry_is_filled_but_explicit_empty_stays_revoked(database):
    with transaction() as connection:
        execute(
            connection, "INSERT INTO brands(name,normalized_name) VALUES ('ROSESELSA','roseselsa')"
        )
        model = product_model(connection, "ROSESELSA", "CERAMICS Ultra")
        row = one(
            connection, "SELECT official_domains FROM brands WHERE id=:id", id=model["brand_id"]
        )
        assert "roseselsa.com" in row["official_domains"]
        execute(
            connection,
            "UPDATE brands SET official_domains='[]'::jsonb WHERE id=:id",
            id=model["brand_id"],
        )
        product_model(connection, "ROSESELSA", "CERAMICS MK2")
        assert (
            one(
                connection, "SELECT official_domains FROM brands WHERE id=:id", id=model["brand_id"]
            )["official_domains"]
            == []
        )


def test_unknown_manufacturer_does_not_receive_official_authority(database):
    with transaction() as connection:
        model = product_model(connection, "Unreviewed Company", "Model 1")
        assert (
            one(
                connection, "SELECT official_domains FROM brands WHERE id=:id", id=model["brand_id"]
            )["official_domains"]
            is None
        )
