"""P6: identity mapping is default-deny; directory semantics (nesting, deactivation, fingerprints)."""

import pytest

from analytics_agent.copilot.identity import (
    Directory,
    DirectoryReplica,
    IdentityError,
    resolve_email,
    resolve_slack_user,
)


@pytest.fixture()
def d():
    return Directory.from_config()


def prof(email=None, **kw):
    p = {"id": "U1", "team_id": "T0CITY", "is_bot": False, "deleted": False, "profile": {}}
    if email:
        p["profile"]["email"] = email
    p.update(kw)
    return p


@pytest.mark.parametrize("profile,why", [
    (None, "no profile"),
    (prof(None), "no email"),
    (prof("nobody@cityops.example"), "unknown"),
    (prof("tom.walsh@cityops.example"), "deactivated"),
    (prof("pat.lee@cityops.example"), "ambiguous"),
    (prof("lena.fischer@cityops.example", is_bot=True), "bot"),
    (prof("lena.fischer@cityops.example", deleted=True), "deleted"),
    (prof("lena.fischer@cityops.example", is_restricted=True), "guest"),
    (prof("lena.fischer@cityops.example", id="USLACKBOT"), "slackbot"),
    (prof(None, real_name="Lena Fischer", profile={"display_name": "Lena Fischer"}), "display name only"),
])
def test_mapping_failures_are_denied(d, profile, why):
    with pytest.raises(IdentityError):
        resolve_slack_user(profile, d.state)


def test_alias_and_case_map_to_one_user(d):
    assert resolve_slack_user(prof("ARivera@CityOps.example"), d.state).user_id == "alex"
    assert resolve_email("maria.chen@cityops.example", d.state).user_id == "maria"


def test_nested_groups_and_cycles(d):
    assert "dept-dss" in d.state.effective_groups("rosa")  # via dss-forestry
    assert d.state.data_departments("rosa") == {"DSS"}
    d.add_member("dss-forestry", "dept-dss")  # a cycle must not loop forever
    assert "dept-dss" in d.state.effective_groups("rosa")


def test_deactivation_removes_everything(d):
    fp = d.state.fingerprint("maria")
    d.deactivate("maria")
    assert d.state.effective_groups("maria") == frozenset()
    assert d.state.principals("maria") == frozenset()
    assert d.state.fingerprint("maria") != fp


def test_replica_lags_then_catches_up():
    t = [1000.0]
    d = Directory.from_config(clock=lambda: t[0])
    r = DirectoryReplica(d, lag_s=300)
    r.sync(now=t[0] + 300)
    t[0] += 10
    d.remove_member("dept-cdot", "maria")
    r.sync(now=t[0])
    assert "dept-cdot" in r.state.effective_groups("maria")  # stale inside the window
    r.sync(now=t[0] + 301)
    assert "dept-cdot" not in r.state.effective_groups("maria")


def test_local_identity_override(tmp_path, d):
    from analytics_agent.copilot.identity import apply_local_identity

    p = tmp_path / "li.yaml"
    p.write_text("mappings:\n  - {slack_email: Owner@Real.example, as_user: priya}\n")
    apply_local_identity(d, p)
    assert resolve_slack_user(prof("owner@real.example"), d.state).user_id == "priya"
    p.write_text("mappings:\n  - {slack_email: maria.chen@cityops.example, as_user: priya}\n")
    with pytest.raises(ValueError):
        apply_local_identity(d, p)
