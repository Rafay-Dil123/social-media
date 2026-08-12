from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.common.exceptions import ValidationError
from apps.follows import services
from apps.follows.models import Follow
from apps.follows.selectors import celebrity_followee_ids, followee_ids

pytestmark = pytest.mark.django_db


def _user(n: str) -> User:
    return User.objects.create_user(username=n, email=f"{n}@x.com", password="pw12345678")


def test_follow_creates_edge():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    assert Follow.objects.filter(follower=a, following=b).exists()
    assert b.id in followee_ids(a.id)


def test_follow_is_idempotent():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    services.follow(a, b.id)
    assert Follow.objects.filter(follower=a, following=b).count() == 1


def test_cannot_self_follow():
    a = _user("a")
    with pytest.raises(ValidationError):
        services.follow(a, a.id)


def test_unfollow_removes_edge_and_is_idempotent():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    services.unfollow(a, b.id)
    services.unfollow(a, b.id)  # no error second time
    assert not Follow.objects.filter(follower=a, following=b).exists()


def test_follow_updates_counts():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    a.refresh_from_db(); b.refresh_from_db()
    assert b.follower_count == 1
    assert a.following_count == 1


def test_follow_idempotent_does_not_double_count():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    services.follow(a, b.id)
    b.refresh_from_db()
    assert b.follower_count == 1  # not 2


def test_unfollow_decrements_and_never_goes_negative():
    a, b = _user("a"), _user("b")
    services.follow(a, b.id)
    services.unfollow(a, b.id)
    services.unfollow(a, b.id)  # extra unfollow must not push below zero
    b.refresh_from_db()
    assert b.follower_count == 0


def test_celebrity_flag_flips_at_threshold():
    a, star = _user("a"), _user("star")
    # Seed the star just below the threshold; one more follow crosses it.
    User.objects.filter(pk=star.id).update(
        follower_count=services.CELEBRITY_THRESHOLD - 1
    )
    services.follow(a, star.id)
    star.refresh_from_db()
    assert star.is_fanout_on_read is True


def test_celebrity_followee_ids_selector():
    a, star, normal = _user("a"), _user("star"), _user("normal")
    User.objects.filter(pk=star.id).update(is_fanout_on_read=True)
    services.follow(a, star.id)
    services.follow(a, normal.id)
    assert celebrity_followee_ids(a.id) == [star.id]
