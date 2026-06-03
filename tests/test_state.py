import json
from state import load_state, save_state


def test_load_state_missing_file(tmp_path):
    path = tmp_path / "state.json"
    result = load_state(path)
    assert result == {"services": {}}


def test_load_state_valid_json(tmp_path):
    path = tmp_path / "state.json"
    data = {"services": {"sherlock-hq": {"status": "healthy"}}}
    path.write_text(json.dumps(data))
    result = load_state(path)
    assert result == data


def test_load_state_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad json")
    result = load_state(path)
    assert result == {"services": {}}


def test_save_state_creates_file(tmp_path):
    path = tmp_path / "subdir" / "state.json"
    data = {"services": {"test": {"status": "degraded"}}, "last_run": "now"}
    save_state(data, path)
    assert path.exists()
    assert json.loads(path.read_text()) == data


def test_save_state_atomic(tmp_path):
    """Verify no .tmp file left behind after save."""
    path = tmp_path / "state.json"
    save_state({"services": {}}, path)
    assert not path.with_suffix(".json.tmp").exists()
