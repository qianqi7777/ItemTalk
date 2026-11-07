"""Reviewed manufacturer identities, never populated from model or search output."""

from app.security import normalize

ROSESELSA_ALIASES = ("弱水时砂", "rose technics", "rosetechnics", "roseselsa")

BRANDS = (
    (
        ("迈从", "mchose", "迈从 (mchose)", "迈从(mchose)"),
        ("maicong.cn", "mchose.com.cn", "mchose.store"),
    ),
    (("漫步者", "edifier", "hecate"), ("edifier.com", "edifier.com.cn", "hecategaming.com")),
    (("索尼", "sony"), ("sony.com", "sony.com.cn", "sony.net")),
    (("罗技", "logitech", "logi"), ("logitech.com", "logitech.com.cn", "logi.com")),
    # Both former domains redirect to manufacturer-owned ROSESELSA sites.
    # https://global.roseselsa.com/pages/about-us
    (
        ROSESELSA_ALIASES,
        ("roseselsa.com", "rosetechnic.com", "rosetechnics.com"),
    ),
)


def reviewed_domains(brand):
    key = normalize(brand)
    return next((list(domains) for aliases, domains in BRANDS if key in aliases), None)


def reviewed_identity_aliases(brand):
    """Same-brand names only: sharing manufacturer domains is not identity equivalence."""
    key = normalize(brand)
    return ROSESELSA_ALIASES if key in ROSESELSA_ALIASES else (key,)


def reviewed_entry_pages(brand):
    """Fixed navigation seeds only; these pages do not themselves prove a model."""
    if normalize(brand) in ROSESELSA_ALIASES:
        return ["https://global.roseselsa.com/", "https://global.roseselsa.com/pages/downloads"]
    return []
