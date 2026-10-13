"""恢复核验接受 PostgreSQL 等价数组转换，仍拒绝约束字典变化。"""

from app.restore_check import canonical_constraint


def test_dump_array_cast_normalization_preserves_allowed_values():
    original = (
        "CHECK (state = ANY ((ARRAY['ready'::character varying, "
        "'failed'::character varying])::text[]))"
    )
    restored = (
        "CHECK (state = ANY (ARRAY[('ready'::character varying)::text, "
        "('failed'::character varying)::text]))"
    )
    assert canonical_constraint(original) == restored
    changed = restored.replace("'failed'", "'unsafe'")
    assert canonical_constraint(original) != canonical_constraint(changed)


def test_other_constraint_changes_remain_visible():
    assert canonical_constraint("CHECK (page_no >= 1)") != canonical_constraint(
        "CHECK (page_no >= 0)"
    )
