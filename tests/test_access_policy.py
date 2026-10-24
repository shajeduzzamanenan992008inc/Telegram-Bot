from types import SimpleNamespace

from enan_ultra.security import is_authorized, is_admin


def test_public_access_mode_intentionally_allows_non_admin_users() -> None:
    settings = SimpleNamespace(
        effective_admin_user_ids=frozenset({100}),
        access_mode="public",
        allowed_user_ids=frozenset(),
    )

    assert is_authorized(settings, 200)
    assert is_admin(settings, 100)


def test_allowlist_mode_denies_unknown_users_and_keeps_admin_access() -> None:
    settings = SimpleNamespace(
        effective_admin_user_ids=frozenset({100}),
        access_mode="allowlist",
        allowed_user_ids=frozenset({200}),
    )

    assert is_authorized(settings, 200)
    assert not is_authorized(settings, 300)
    assert is_authorized(settings, 100)
