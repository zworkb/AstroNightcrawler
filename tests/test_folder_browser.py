"""Tests for the folder browser dialog."""

from pathlib import Path

from src.ui.folder_browser import DirectoryEntry, list_directory


class TestListDirectory:
    """Tests for list_directory utility function."""

    def test_lists_subdirectories(self, tmp_path: Path) -> None:
        """Subdirectories appear in the listing."""
        (tmp_path / "subdir1").mkdir()
        (tmp_path / "subdir2").mkdir()
        (tmp_path / "file.txt").write_text("x")
        entries = list_directory(tmp_path)
        dirs = [e for e in entries if e.is_dir and e.name != ".."]
        assert len(dirs) == 2

    def test_parent_entry(self, tmp_path: Path) -> None:
        """Non-root directories start with '..' entry."""
        child = tmp_path / "child"
        child.mkdir()
        entries = list_directory(child)
        assert entries[0].name == ".."
        assert entries[0].is_dir

    def test_marks_manifest_dirs(self, tmp_path: Path) -> None:
        """Directories containing manifest.json are flagged."""
        seq_dir = tmp_path / "deneb"
        seq_dir.mkdir()
        (seq_dir / "manifest.json").write_text("{}")
        entries = list_directory(tmp_path)
        deneb = [e for e in entries if e.name == "deneb"][0]
        assert deneb.has_manifest

    def test_no_parent_at_root(self) -> None:
        """Root directory does not include '..' entry."""
        entries = list_directory(Path("/"))
        names = [e.name for e in entries]
        assert ".." not in names

    def test_dirs_before_files(self, tmp_path: Path) -> None:
        """Directories appear before files in the listing."""
        (tmp_path / "zzz_dir").mkdir()
        (tmp_path / "aaa_file.txt").write_text("x")
        entries = list_directory(tmp_path)
        # Skip '..' entry
        non_parent = [e for e in entries if e.name != ".."]
        assert non_parent[0].is_dir
        assert not non_parent[1].is_dir

    def test_hidden_files_excluded(self, tmp_path: Path) -> None:
        """Hidden files (starting with .) are not listed."""
        (tmp_path / ".hidden").write_text("x")
        (tmp_path / "visible.txt").write_text("x")
        entries = list_directory(tmp_path)
        names = [e.name for e in entries]
        assert ".hidden" not in names
        assert "visible.txt" in names

    def test_file_size(self, tmp_path: Path) -> None:
        """File entries include size information."""
        content = "hello world"
        (tmp_path / "test.txt").write_text(content)
        entries = list_directory(tmp_path)
        file_entry = [e for e in entries if e.name == "test.txt"][0]
        assert file_entry.size == len(content)

    def test_directory_entry_dataclass(self) -> None:
        """DirectoryEntry stores all expected fields."""
        entry = DirectoryEntry(
            name="test", path=Path("/tmp/test"),
            is_dir=True, has_manifest=True, size=0,
        )
        assert entry.name == "test"
        assert entry.has_manifest
