"""Personalized messages: fields from each recipient's row (personal.py), wired into API sends."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import personal as pz  # noqa: E402
from test_api_sending import FakeAPI, api_agent  # noqa: E402,F401
from test_v36 import agent, write  # noqa: E402,F401

# Like the Black Friday sheet: "User Details" is a merged header over 3 columns.
GRID = [
    ["Full Name", "Email", "Order Value", "Country", "User Details", "", "", "Status"],
    ["Ann Lee", "ann@x.com", "$1,088.16", "Canada", "PostX Users", "WowStore AppSumo", "All WooCommerce Users", ""],
    ["Bo Chan", "bo@x.com", "$150.00", "", "PostX Users", "PostX Deactivated", "Subscribed", ""],
    ["Cy Doe", "cy@x.com", "-", "", "", "", "", ""],
]
BUNDLE = "{bundle: PostX, WowStore, WholesaleX}\n"


def info(email):
    return pz.build_index(GRID)[0][email]


def test_merged_header_cells_are_read_as_one_field():
    index, headers = pz.build_index(GRID)
    assert headers == ["Full Name", "Email", "Order Value", "Country", "User Details", "Status"]
    assert index["ann@x.com"]["fields"]["User Details"] == "PostX Users, WowStore AppSumo, All WooCommerce Users"
    assert pz.plugins_of(index["ann@x.com"]) == ["PostX", "WowStore"]          # "All WooCommerce" is not a plugin


def test_fields_conditions_and_missing_plugins():
    text = (BUNDLE + "Hi {first_name},\n{if spent >= 599}You spent {spent} already.{else}Welcome back.{endif}\n"
            "You use {plugins}; missing: {missing_plugins}. From {Country|your country}."
            "{if tags contains WooCommerce} Store owner.{endif}")
    ann, _ = pz.render(text, "Ann Lee", info("ann@x.com"))
    assert ann == ("Hi Ann,\nYou spent $1,088.16 already.\nYou use PostX and WowStore; missing: WholesaleX. "
                   "From Canada. Store owner.")
    bo, empty = pz.render(text, "Bo Chan", info("bo@x.com"))
    assert "Welcome back." in bo and "From your country." in bo and "Store owner" not in bo
    assert "missing: WowStore and WholesaleX" in bo and empty == []


def test_dash_value_counts_as_empty_and_is_reported():
    out, empty = pz.render("Spent {spent}.{if spent > 0} Thanks!{endif}", "Cy", info("cy@x.com"))
    assert out == "Spent ." and empty == ["spent"]


def test_check_catches_typos_and_unclosed_if():
    headers = pz.build_index(GRID)[1]
    assert pz.check(["Hi {first_name} {Country} {spent} {ai_opener}"], headers) == []
    problems = " ".join(pz.check(["{Countyr} {if spent > 5}x", "{missing_plugins}"], headers))
    assert "{Countyr}" in problems and "{endif}" in problems and "bundle list" in problems


def test_api_send_fills_each_persons_row(api_agent, tmp_path):
    path = write(tmp_path / "r.csv", ["Full Name", "Contact Email", "Order Value", "User Details", ""],
                 [["Ann Lee", "ann@x.com", "$900", "PostX Users", "WowStore Users"],
                  ["Bo Chan", "bo@x.com", "$100", "PostX Users", ""]])
    mod = api_agent(path, messages=(BUNDLE + "Hi {first_name}, add {missing_plugins}."
                                    "{if Order Value >= 599} VIP.{endif}",))
    mod.main()
    texts = [c[2] for c in FakeAPI.calls if c[0] == "send"]
    assert texts == ["Hi Ann, add WholesaleX. VIP.", "Hi Bo, add WowStore and WholesaleX."]


def test_unknown_field_stops_before_any_send(api_agent, tmp_path):
    path = write(tmp_path / "r.csv", ["Full Name", "Contact Email"], [["Ann Lee", "ann@x.com"]])
    mod = api_agent(path, messages=("Hi {first_name} from {Country}",))
    with pytest.raises(RuntimeError, match="Country"):
        mod.main()
    assert not [c for c in FakeAPI.calls if c[0] in ("create", "send")]


def test_an_if_without_endif_is_named_not_called_a_missing_column():
    import personal as pz
    text = "{if Pro Plugins}A{endif}\n{if Remaining Plugins}Try {Remaining Plugins}.\nSee: https://x.test/?ref=1"
    problems = pz.check([text], ["Pro Plugins", "Remaining Plugins"])
    assert any(p.startswith("{if Remaining Plugins} has no {endif}") for p in problems)
    assert not any("not a column" in p for p in problems)
