import json

from tacit.sources import read_sources


def test_sources_skip_hidden_files_symlinks_and_credentials(tmp_path):
    root = tmp_path / "context"
    root.mkdir()
    (root / "notes.md").write_text("B17 uses p2")
    (root / ".env").write_text("TOKEN=private")
    (root / "auth.json").write_text('{"token":"private"}')
    outside = tmp_path / "outside.md"
    outside.write_text("private outside data")
    (root / "linked.md").symlink_to(outside)
    sources = read_sources(root)
    assert sources == [{"path": "notes.md", "content": "B17 uses p2", "truncated": False}]
    assert "private" not in json.dumps(sources)


def test_sources_enforce_input_budget(tmp_path):
    (tmp_path / "a.md").write_text("A" * 50)
    (tmp_path / "b.md").write_text("B" * 50)
    sources = read_sources(tmp_path, per_file=10, max_chars=15)
    assert sum(len(source["content"]) for source in sources) == 15
    assert all(source["truncated"] for source in sources)
