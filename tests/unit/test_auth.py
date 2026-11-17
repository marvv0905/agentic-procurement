import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from procurement import auth
from procurement.repository import User, init_db, session_factory


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    return session_factory(engine)()


def test_hash_and_verify_roundtrip():
    stored = auth.hash_password("s3cret!")
    assert stored != "s3cret!"
    assert auth.verify_password("s3cret!", stored)
    assert not auth.verify_password("wrong", stored)


def test_hashes_are_salted():
    assert auth.hash_password("same") != auth.hash_password("same")


def test_malformed_hash_fails_closed():
    assert not auth.verify_password("x", "not-a-valid-hash")


def test_create_user_and_login():
    session = _session()
    user = auth.create_user(
        session,
        username="alice",
        display_name="Alice Chan",
        role=auth.ROLE_EMPLOYEE,
        password="hunter2",
    )
    assert user.active is True

    assert auth.login(session, "alice", "hunter2") is not None
    assert auth.login(session, "alice", "nope") is None
    assert auth.login(session, "ghost", "hunter2") is None


def test_create_user_stores_department():
    session = _session()
    user = auth.create_user(
        session,
        username="carol",
        display_name="Carol Lee",
        role=auth.ROLE_EMPLOYEE,
        password="pw",
        department="Accounting",
    )
    assert user.department == "Accounting"

    bare = auth.create_user(
        session, username="dan", display_name="Dan", role=auth.ROLE_EMPLOYEE, password="pw"
    )
    assert bare.department is None


def test_login_rejects_inactive_user():
    session = _session()
    user = auth.create_user(
        session, username="bob", display_name="Bob", role="manager", password="pw"
    )
    user.active = False
    session.commit()

    assert auth.login(session, "bob", "pw") is None


def test_create_user_rejects_bad_input():
    session = _session()
    with pytest.raises(ValueError):
        auth.create_user(
            session, username="", display_name="X", role="manager", password="pw"
        )
    with pytest.raises(ValueError):
        auth.create_user(
            session, username="carol", display_name="C", role="wizard", password="pw"
        )


def test_create_user_rejects_duplicate_username():
    session = _session()
    auth.create_user(
        session, username="dan", display_name="Dan", role="employee", password="pw"
    )
    with pytest.raises(ValueError):
        auth.create_user(
            session, username="dan", display_name="Dan 2", role="employee", password="pw"
        )


def test_seeded_roles_are_known():
    assert auth.ROLES == ("manager", "employee")