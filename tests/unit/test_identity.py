from dqflow.pipeline import code_hash


def test_dependency_and_template_changes_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src/dqflow/templates").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("version=1")
    lock = tmp_path / "uv.lock"
    lock.write_text("locked dependency 1")
    original = code_hash()
    assert code_hash() == original
    lock.write_text("locked dependency 2")
    updated = code_hash()
    assert updated != original
    (tmp_path / "src/dqflow/templates/report.html").write_text("new template")
    assert code_hash() != updated
