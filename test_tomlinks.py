#!/usr/bin/env python3

import pytest
import tempfile
import json
import os
import shutil
from pathlib import Path
import tomlinks


class TestParsing:
    """Test INI parsing and ~ expansion."""
    
    def test_parse_simple_mapping(self, tmp_path):
        """Single file mapping."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        ini = pkg / "tomlinks.ini"
        ini.write_text("config.fish = ~/.config/fish/config.fish\n")
        
        section = tomlinks.parse(pkg)
        assert "config.fish" in section
        assert section["config.fish"] == os.path.expanduser("~/.config/fish/config.fish")
    
    def test_parse_multiple_mappings(self, tmp_path):
        """Multiple files in one package."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        ini = pkg / "tomlinks.ini"
        ini.write_text("""
config.fish = ~/.config/fish/config.fish
functions/ = ~/.config/fish/functions/
""")
        
        section = tomlinks.parse(pkg)
        assert len(section) == 2
        assert "config.fish" in section
        assert "functions/" in section
    
    def test_parse_missing_ini(self, tmp_path):
        """Package with empty ini file (no unnamed section) should raise."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        # Empty ini → no unnamed section
        (pkg / "tomlinks.ini").write_text("")
        with pytest.raises(tomlinks.TomlinksException, match="there is no unnamed section"):
            tomlinks.parse(str(pkg))
    
    def test_parse_empty_ini(self, tmp_path):
        """Empty INI should raise."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "tomlinks.ini").write_text("")
        
        with pytest.raises(tomlinks.TomlinksException, match="no unnamed section"):
            tomlinks.parse(pkg)
    
    def test_tilda_expansion(self, tmp_path):
        """~ should expand to home."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        ini = pkg / "tomlinks.ini"
        ini.write_text("file = ~/test\n")
        
        section = tomlinks.parse(pkg)
        assert "~" not in section["file"]
        assert section["file"].startswith(os.path.expanduser("~"))


class TestRestore:
    """Test restore: package → system."""
    
    def test_restore_single_file(self, tmp_path):
        """Restore one file from package to system."""
        # Setup package
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "tomlinks.ini").write_text(f"testfile = {tmp_path / 'dest' / 'testfile'}\n")
        (pkg / "testfile").write_text("content")
        
        # Restore
        tomlinks.restore(str(pkg))
        
        # Verify
        dest = tmp_path / "dest" / "testfile"
        assert dest.exists()
        assert dest.read_text() == "content"
    
    def test_restore_directory(self, tmp_path):
        """Restore entire directory."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "tomlinks.ini").write_text(f"dir/ = {tmp_path / 'dest' / 'dir/'}\n")
        (pkg / "dir").mkdir()
        (pkg / "dir" / "file1.txt").write_text("one")
        (pkg / "dir" / "file2.txt").write_text("two")
        
        tomlinks.restore(str(pkg))
        
        dest = tmp_path / "dest" / "dir"
        assert (dest / "file1.txt").read_text() == "one"
        assert (dest / "file2.txt").read_text() == "two"
    
    def test_restore_overwrites_existing(self, tmp_path):
        """Restore should replace existing destination."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        dest_file = dest / "testfile"
        dest_file.write_text("old")
        
        (pkg / "tomlinks.ini").write_text(f"testfile = {dest_file}\n")
        (pkg / "testfile").write_text("new")
        
        tomlinks.restore(str(pkg))
        
        assert dest_file.read_text() == "new"
    
    def test_restore_creates_parent_dirs(self, tmp_path):
        """Restore should create missing parent directories."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest = tmp_path / "deep" / "nested" / "path" / "file"
        (pkg / "tomlinks.ini").write_text(f"testfile = {dest}\n")
        (pkg / "testfile").write_text("content")
        
        tomlinks.restore(str(pkg))
        
        assert dest.exists()
        assert dest.read_text() == "content"
    
    def test_restore_missing_source_file(self, tmp_path):
        """Missing source file should be skipped (warning printed, no exception)."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"missing = {dest}\n")
        # Don't create 'missing' file
        ops, parents = tomlinks._plan("restore", [str(pkg)])
        # Should skip missing file → empty operations
        assert ops == []
    
    def test_restore_multiple_packages(self, tmp_path):
        """Restore several packages at once."""
        pkg1 = tmp_path / "pkg1"
        pkg1.mkdir()
        (pkg1 / "tomlinks.ini").write_text(f"file1 = {tmp_path / 'dest1'}\n")
        (pkg1 / "file1").write_text("one")
        
        pkg2 = tmp_path / "pkg2"
        pkg2.mkdir()
        (pkg2 / "tomlinks.ini").write_text(f"file2 = {tmp_path / 'dest2'}\n")
        (pkg2 / "file2").write_text("two")
        
        tomlinks.restore(str(pkg1), str(pkg2))
        
        assert (tmp_path / "dest1").read_text() == "one"
        assert (tmp_path / "dest2").read_text() == "two"


class TestCollect:
    """Test collect: system → package."""
    
    def test_collect_single_file(self, tmp_path):
        """Collect one file from system into package."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        src = tmp_path / "system" / "config"
        src.parent.mkdir()
        src.write_text("config_content")
        
        (pkg / "tomlinks.ini").write_text(f"config = {src}\n")
        
        tomlinks.collect(str(pkg))
        
        assert (pkg / "config").read_text() == "config_content"
    
    def test_collect_directory(self, tmp_path):
        """Collect entire directory."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        src = tmp_path / "system" / "dir"
        src.mkdir(parents=True)
        (src / "file1").write_text("one")
        (src / "file2").write_text("two")
        
        (pkg / "tomlinks.ini").write_text(f"dir/ = {src}/\n")
        
        tomlinks.collect(str(pkg))
        
        assert (pkg / "dir" / "file1").read_text() == "one"
        assert (pkg / "dir" / "file2").read_text() == "two"
    
    def test_collect_overwrites_package_file(self, tmp_path):
        """Collect should replace existing file in package."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "config").write_text("old")
        
        src = tmp_path / "system" / "config"
        src.parent.mkdir()
        src.write_text("new")
        
        (pkg / "tomlinks.ini").write_text(f"config = {src}\n")
        
        tomlinks.collect(str(pkg))
        
        assert (pkg / "config").read_text() == "new"
    
    def test_collect_missing_source(self, tmp_path):
        """Missing system file should be skipped (warning printed, no exception)."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        missing_sys = tmp_path / "missing"
        (pkg / "tomlinks.ini").write_text(f"config = {missing_sys}\n")
        ops, parents = tomlinks._plan("collect", [str(pkg)])
        assert ops == []


class TestTransactions:
    """Test transaction rollback and recovery."""
    
    def test_transaction_rollback_on_error(self, tmp_path, monkeypatch):
        """Failed operation should rollback completed steps."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest1 = tmp_path / "dest1"
        dest2 = tmp_path / "dest2"
        (pkg / "tomlinks.ini").write_text(f"""
file1 = {dest1}
file2 = {dest2}
""")
        (pkg / "file1").write_text("one")
        (pkg / "file2").write_text("two")
        # Force error by making shutil.copy2 fail on second file
        original_copy = shutil.copy2
        call_count = [0]
        def failing_copy(src, dst, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise OSError("Simulated failure")
            return original_copy(src, dst, *args, **kwargs)
        monkeypatch.setattr(shutil, "copy2", failing_copy)
        with pytest.raises(tomlinks.TomlinksException):
            tomlinks.restore(str(pkg))
        # Rollback should clean up dest1
        assert not dest1.exists()
    
    def test_empty_operations(self, tmp_path):
        """No operations should not crash."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "tomlinks.ini").write_text("# empty\n")
        (pkg / "tomlinks.ini").write_text("")
        
        # Should not raise, just skip
        with pytest.raises(tomlinks.TomlinksException):
            tomlinks.restore(str(pkg))
    
    def test_transaction_journal_created(self, tmp_path, monkeypatch):
        """Transaction should create workspace and journal."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"file = {dest}\n")
        (pkg / "file").write_text("content")
        # Intercept before cleanup
        original_cleanup = tomlinks._cleanup_transaction
        journal_copy = {}
        def capture_journal(journal):
            journal_copy.update(journal)
            original_cleanup(journal)
        monkeypatch.setattr(tomlinks, "_cleanup_transaction", capture_journal)
        
        tomlinks.restore(str(pkg))
        
        assert journal_copy["version"] == 1
        assert "operations" in journal_copy
        assert len(journal_copy["operations"]) == 1


class TestEdgeCases:
    """Edge cases and boundary conditions."""
    
    def test_restore_symlink_source(self, tmp_path):
        """Package file is a symlink."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        real_file = tmp_path / "real"
        real_file.write_text("content")
        link = pkg / "link"
        link.symlink_to(real_file)
        
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"link = {dest}\n")
        
        tomlinks.restore(str(pkg))
        
        # Should copy symlink target content
        assert dest.read_text() == "content"
    
    def test_absolute_paths_in_package(self, tmp_path):
        """Package with absolute source paths."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        abs_source = tmp_path / "absolute_file"
        abs_source.write_text("abs")
        
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"{abs_source} = {dest}\n")
        
        tomlinks.restore(str(pkg))
        
        assert dest.read_text() == "abs"
    
    def test_unicode_filenames(self, tmp_path):
        """Files with unicode names."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        unicode_name = "файл_测试.txt"
        (pkg / unicode_name).write_text("unicode")
        
        dest = tmp_path / unicode_name
        (pkg / "tomlinks.ini").write_text(f"{unicode_name} = {dest}\n")
        
        tomlinks.restore(str(pkg))
        
        assert dest.read_text() == "unicode"
    
    def test_whitespace_in_paths(self, tmp_path):
        """Paths with spaces."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "file with spaces").write_text("content")
        
        dest = tmp_path / "dest with spaces"
        (pkg / "tomlinks.ini").write_text(f"file with spaces = {dest}\n")
        
        tomlinks.restore(str(pkg))
        
        assert dest.read_text() == "content"
    
    def test_nested_directory_restore(self, tmp_path):
        """Deep directory structure."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        nested = pkg / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "deep.txt").write_text("deep")
        
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"a/ = {dest}/a/\n")
        
        tomlinks.restore(str(pkg))
        
        assert (dest / "a" / "b" / "c" / "deep.txt").read_text() == "deep"


class TestHelpers:
    """Test internal helper functions."""
    
    def test_absolute_path(self):
        """_absolute should normalize paths."""
        result = tomlinks._absolute("./test/../file")
        assert ".." not in result
        assert result == os.path.normpath(os.path.abspath("file"))
    
    def test_inside_path(self, tmp_path):
        """_inside should detect path containment."""
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        
        assert tomlinks._inside(str(child), str(parent))
        assert not tomlinks._inside(str(parent), str(child))
    
    def test_missing_parent_dirs(self, tmp_path):
        """_missing_parent_dirs should list missing ancestors."""
        path = tmp_path / "a" / "b" / "c" / "file"
        missing = tomlinks._missing_parent_dirs(str(path))
        
        assert str(tmp_path / "a") in missing
        assert str(tmp_path / "a" / "b") in missing
        assert str(tmp_path / "a" / "b" / "c") in missing


class TestCLI:
    """Test command-line interface."""
    
    def test_main_no_args(self, capsys):
        """No arguments should show help and return 0."""
        result = tomlinks.main([])
        assert result == 0
        
        captured = capsys.readouterr()
        assert "Usage:" in captured.out
    
    def test_main_help(self, capsys):
        """--help should show help."""
        result = tomlinks.main(["--help"])
        assert result == 0
        
        captured = capsys.readouterr()
        assert "Usage:" in captured.out
    
    def test_main_restore(self, tmp_path):
        """CLI restore command."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        dest = tmp_path / "dest"
        (pkg / "tomlinks.ini").write_text(f"file = {dest}\n")
        (pkg / "file").write_text("cli")
        
        result = tomlinks.main(["restore", str(pkg)])
        assert result == 0
        assert dest.read_text() == "cli"
    
    def test_main_collect(self, tmp_path):
        """CLI collect command."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        src = tmp_path / "src"
        src.write_text("cli_collect")
        (pkg / "tomlinks.ini").write_text(f"file = {src}\n")
        
        result = tomlinks.main(["collect", str(pkg)])
        assert result == 0
        assert (pkg / "file").read_text() == "cli_collect"
