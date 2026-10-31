"""The settings file: defaults, round trip (save, download, upload, restore), validation, migration, diff."""

import json

import pytest

from caltreat.config import Config, defaults, diff, parse, validate


def test_defaults_are_valid():
    assert validate(defaults()) == []


def test_round_trip(tmp_path):
    cfg = Config.load(tmp_path)
    assert cfg.error is None and not cfg.path.exists()  # no file yet: defaults, nothing written
    cfg.data["mix"]["horn"] = 90
    cfg.data["speakers"]["inside"] = {"device": "usb:1.3", "ch": "right", "level": 70}
    cfg.data["functions"].append({"id": "smoke", "name": "Smoke", "kind": "manual", "symbol": "●"})
    cfg.data["relay"][3] = {"fn": "smoke", "alt": False}
    cfg.save()
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]  # atomic write leaves no temp file

    again = Config.load(tmp_path)
    assert again.data == cfg.data

    exported = again.export()  # what the Download button sends
    new, errs = parse(exported)  # what the Upload button does
    assert errs == [] and new == cfg.data

    other = Config.load(tmp_path / "other")
    other.replace(new)  # restore on another Pi
    assert Config.load(tmp_path / "other").data == cfg.data


def test_partial_and_old_files_get_defaults():
    new, errs = parse(json.dumps({"version": 1, "mix": {"bell": 50}}))
    assert errs == []
    assert new["mix"]["bell"] == 50 and new["mix"]["horn"] == 100
    assert new["speakers"]["front"]["device"] == "jack"
    assert {f["id"] for f in new["functions"]} >= {"head", "markers", "doors"}


@pytest.mark.parametrize("bad, words", [
    ("not json", "not JSON"),
    ("[]", "not a JSON object"),
    ({"mix": {}}, "version"),
    ({"version": 99}, "newer"),
    ({"version": 1, "mix": {"horn": 500}}, "mix.horn"),
    ({"version": 1, "mix": {"cowbell": 50}}, "mix.cowbell"),
    ({"version": 1, "speakers": {"front": {"device": "usb:../../etc", "ch": "both", "level": 1}}}, "device"),
    ({"version": 1, "speakers": {"attic": {"device": "jack"}}}, "speakers.attic"),
    ({"version": 1, "speakers": {"rear": {"device": "jack", "ch": "middle"}}}, "speakers.rear.ch"),
    ({"version": 1, "relay": [{"fn": "head"}]}, "relay"),
    ({"version": 1, "relay": [{"fn": "laser"}, {}, {}, {}]}, "unknown function"),
    ({"version": 1, "functions": [{"id": "Bad Id!", "name": "x"}]}, "functions[0].id"),
    ({"version": 1, "functions": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, "used twice"),
    ({"version": 1, "functions": [{"id": "a", "name": "A", "kind": "laser"}]}, "kind"),
    ({"version": 1, "boards": {"zz": {"name": "A"}}}, "chip ID"),
    ({"version": 1, "boards": {"AB12": {"name": "A", "outputs": [{}] * 8}, "CD34": {"name": "A",
                                                                                  "outputs": [{}] * 8}}}, "used twice"),
    ({"version": 1, "boards": {"AB12": {"name": "A", "outputs": [{}] * 3}}}, "outputs"),
    ({"version": 1, "intercom": {"duck_db": 10}}, "intercom.duck_db"),
    ({"version": 1, "depart_horn": "yes"}, "depart_horn"),
])
def test_bad_files_are_rejected(bad, words):
    new, errs = parse(bad if isinstance(bad, str) else json.dumps(bad))
    assert new is None
    assert any(words in e for e in errs), errs


def test_broken_file_on_disk_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{ broken")
    cfg = Config.load(tmp_path)
    assert cfg.error and "unreadable" in cfg.error
    assert cfg.data == defaults()


def test_board_names_migrate_from_boards_json(tmp_path):
    (tmp_path / "boards.json").write_text(json.dumps({"E6614103E7A1B2C3": "A", "E6614103E7000001": "B"}))
    cfg = Config.load(tmp_path)
    assert cfg.data["boards"]["E6614103E7A1B2C3"]["name"] == "A"
    assert cfg.data["boards"]["E6614103E7000001"]["name"] == "B"
    assert len(cfg.data["boards"]["E6614103E7000001"]["outputs"]) == 8
    assert (tmp_path / "config.json").exists()


def test_reset_keeps_board_names(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.data["boards"]["AB12"] = {"name": "Front", "outputs": [{"fn": "head", "alt": False}] * 8}
    cfg.data["mix"]["master"] = 20
    cfg.reset()
    assert cfg.data["mix"]["master"] == 100
    assert cfg.data["boards"]["AB12"]["name"] == "Front"
    assert all(o["fn"] is None for o in cfg.data["boards"]["AB12"]["outputs"])


def test_diff_lists_changes():
    a, b = defaults(), defaults()
    b["mix"]["horn"] = 80
    b["speakers"]["rear"]["device"] = "usb:1.3"
    b["relay"][0]["fn"] = None
    d = diff(a, b)
    assert "mix.horn: 100 -> 80" in d
    assert 'speakers.rear.device: "none" -> "usb:1.3"' in d
    assert 'relay[0].fn: "head" -> null' in d
    assert diff(a, a) == []


def test_listeners_run_on_save(tmp_path):
    cfg = Config.load(tmp_path)
    seen = []
    cfg.on_change(lambda: seen.append(cfg.data["mix"]["bell"]))
    cfg.data["mix"]["bell"] = 10
    cfg.save()
    assert seen == [10]


@pytest.mark.parametrize("text", ['{"version": 2, "mix": {"horn": 33}}', '{"version": 1, "mix": {"horn": 999}}', "{oops"])
def test_unusable_file_is_kept_before_defaults_overwrite_it(tmp_path, text):
    (tmp_path / "config.json").write_text(text)
    cfg = Config.load(tmp_path)
    assert cfg.error and "config.json.bad" in cfg.error
    cfg.save()  # e.g. a board found at boot
    assert (tmp_path / "config.json.bad").read_text() == text
