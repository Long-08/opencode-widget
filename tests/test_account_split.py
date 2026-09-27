"""Tests for data_server._split_own_other(local_rows, remote_rows).

Rule under test: a local row is the logged-in account when it matches an
official row of the same model within +/-120000 ms; one official row may only
absorb a single local row. Matched locals are dropped (official wins);
unmatched locals are kept and tagged account="other".
"""
import helpers

OWN_MATCH_MS = 120000


def test_empty_remote_marks_every_local_other(data_server):
    rows = [
        helpers.make_row(1000, "m1", cost=1.0),
        helpers.make_row(2000, "m2", cost=2.0, account="stale-account"),
    ]
    out = data_server._split_own_other(rows, [])
    assert out == rows
    assert all(r["account"] == "other" for r in out)


def test_matched_local_dropped_unmatched_returned_as_other(data_server):
    same = helpers.make_row(1000, "m1", cost=1.0)
    other = helpers.make_row(10_000_000, "m1", cost=2.0)
    remote = [helpers.make_remote_row(1000, "m1")]
    out = data_server._split_own_other([same, other], remote)
    assert out == [other]
    assert other["account"] == "other"
    # matched (official) row is dropped from the returned extras
    assert same not in out


def test_match_boundary_exactly_120000ms(data_server):
    local = helpers.make_row(OWN_MATCH_MS, "m1")
    out = data_server._split_own_other([local], [helpers.make_remote_row(0, "m1")])
    assert out == []


def test_no_match_one_ms_over_boundary(data_server):
    local = helpers.make_row(OWN_MATCH_MS + 1, "m1")
    out = data_server._split_own_other([local], [helpers.make_remote_row(0, "m1")])
    assert out == [local]
    assert local["account"] == "other"


def test_one_official_matches_at_most_one_local(data_server):
    first = helpers.make_row(1000, "m1")
    second = helpers.make_row(1100, "m1")
    remote = [helpers.make_remote_row(1000, "m1")]
    out = data_server._split_own_other([first, second], remote)
    # only one of the two locals may absorb the single official record
    assert len(out) == 1
    assert out[0] is second
    assert second["account"] == "other"
    assert "account" not in first


def test_different_model_never_matches(data_server):
    local = helpers.make_row(1000, "m1")
    out = data_server._split_own_other([local], [helpers.make_remote_row(1000, "m2")])
    assert out == [local]
    assert local["account"] == "other"


def test_matched_row_with_existing_account_is_dropped(data_server):
    local = helpers.make_row(1000, "m1", account="own")
    out = data_server._split_own_other([local], [helpers.make_remote_row(1000, "m1")])
    assert out == []
