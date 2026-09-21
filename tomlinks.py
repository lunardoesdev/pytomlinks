#!/usr/bin/env python3

import configparser
import errno
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported platform provides fcntl
    fcntl = None
VERSION = "1.0.0"

class TomlinksException(Exception):
    pass


LOCK_PATH = Path(tempfile.gettempdir()) / "tomlinks.lock"
TRANSACTIONS_ROOT = Path.home() / ".cache" / "tomlinks" / "transactions"
JOURNAL_NAME = "journal.json"


def process_tilda(section):
    for k, v in section.items():
        section[k] = v.replace("~", os.path.expanduser("~"))
    return section


def parse(path):
    config = configparser.ConfigParser(allow_unnamed_section=True)
    config.optionxform = str
    config.read(f"{path}/tomlinks.ini")
    if configparser.UNNAMED_SECTION in config:
        section = config[configparser.UNNAMED_SECTION]
        section = process_tilda(section)
        return section
    raise TomlinksException(f"there is no unnamed section in {path}/tomlinks.ini")


def _absolute(path):
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        if exc.errno in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            return
        raise
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
    finally:
        os.close(fd)


def _fsync_file(path):
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())


def _fsync_tree(path):
    if os.path.isdir(path) and not os.path.islink(path):
        for entry in os.scandir(path):
            _fsync_tree(entry.path)
        _fsync_directory(path)
    else:
        _fsync_file(path)


def _remove_path(path):
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def _write_journal(workspace, journal):
    journal_path = workspace / JOURNAL_NAME
    temporary = workspace / (JOURNAL_NAME + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(journal, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, journal_path)
    _fsync_directory(workspace)
    _fsync_directory(workspace.parent)


def _lock():
    if fcntl is None:
        raise TomlinksException("fcntl is required for transactional locking")
    try:
        stream = open(LOCK_PATH, "a+")
    except OSError as exc:
        raise TomlinksException(f"cannot open transaction lock: {exc}") from exc
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        stream.close()
        raise TomlinksException(f"cannot acquire transaction lock: {exc}") from exc
    return stream


def _missing_parent_dirs(path):
    parent = Path(path).parent
    missing = []
    current = parent
    while not os.path.lexists(current):
        missing.append(_absolute(current))
        next_parent = current.parent
        if next_parent == current:
            break
        current = next_parent
    if not os.path.isdir(current):
        raise TomlinksException(f"destination parent is not a directory: {parent}")
    return missing


def _plan(mode, package_paths):
    operations = []
    seen_destinations = set()
    created_parents = set()
    for package_path in package_paths:
        package = _absolute(package_path)
        try:
            entries = parse(package)
        except OSError as exc:
            raise TomlinksException(f"cannot read package {package}: {exc}") from exc
        for key, configured_destination in entries.items():
            source = _absolute(os.path.join(package, key))
            destination = (
                _absolute(configured_destination)
                if mode == "restore"
                else _absolute(os.path.join(package, key))
            )
            if mode == "collect":
                source = _absolute(configured_destination)
            print(f"{source} -> {destination}")
            if destination in seen_destinations:
                raise TomlinksException(f"duplicate destination path: {destination}")
            seen_destinations.add(destination)
            if not os.path.exists(source):
                print(f"not exist: {source}")
                continue
            if not (os.path.isdir(source) or os.path.isfile(source)):
                raise TomlinksException(f"unsupported source type: {source}")
            for parent in _missing_parent_dirs(destination):
                created_parents.add(parent)
            operations.append({"src": source, "dest": destination})
    return operations, sorted(created_parents, key=lambda item: (item.count(os.sep), item))


def _inside(path, directory):
    try:
        Path(path).relative_to(directory)
        return True
    except ValueError:
        return False


def _validate_journal(journal, workspace):
    if not isinstance(journal, dict) or journal.get("version") != 1:
        raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
    if journal.get("workspace") != str(workspace):
        raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
    if journal.get("state") not in {"prepared", "committing", "committed"}:
        raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
    operations = journal.get("operations")
    parents = journal.get("created_parents")
    if not isinstance(operations, list) or not isinstance(parents, list):
        raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
    txid = workspace.name
    destinations = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        required = {"src", "dest", "stage", "backup", "dest_existed", "phase"}
        if not required.issubset(operation):
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        destination = operation["dest"]
        stage = operation["stage"]
        backup = operation["backup"]
        phase = operation["phase"]
        if not all(isinstance(operation[field], str) for field in ("src", "dest", "stage", "backup")):
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        if not isinstance(operation["dest_existed"], bool) or phase not in {
            "planned", "backup_moving", "backup_moved", "installing", "installed"
        }:
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        if not all(os.path.isabs(path) for path in (destination, stage, backup)):
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        if destination in destinations or stage == destination or backup == destination:
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")
        destinations.add(destination)
        destination_parent = _absolute(Path(destination).parent)
        for artifact, label in ((stage, "stage"), (backup, "backup")):
            if not (_inside(artifact, workspace) or _absolute(Path(artifact).parent) == destination_parent):
                raise TomlinksException(f"unsafe {label} path in transaction journal")
            expected_prefix = f".tomlinks-{label}-{txid}-"
            if not _inside(artifact, workspace) and not Path(artifact).name.startswith(expected_prefix):
                raise TomlinksException(f"unsafe {label} path in transaction journal")
    for parent in parents:
        if not isinstance(parent, str) or not os.path.isabs(parent) or parent == os.path.abspath(os.sep):
            raise TomlinksException(f"malformed transaction journal: {workspace / JOURNAL_NAME}")


def _load_journal(workspace):
    journal_path = workspace / JOURNAL_NAME
    try:
        with open(journal_path, encoding="utf-8") as stream:
            journal = json.load(stream)
    except (OSError, ValueError) as exc:
        raise TomlinksException(f"malformed transaction journal: {journal_path}: {exc}") from exc
    _validate_journal(journal, workspace)
    return journal


def _remove_created_parents(journal):
    for parent in sorted(journal["created_parents"], key=lambda item: (item.count(os.sep), item), reverse=True):
        try:
            os.rmdir(parent)
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                raise TomlinksException(f"cannot remove transaction-created parent: {parent}") from exc
            raise


def rollback_transaction(journal):
    workspace = Path(journal["workspace"])
    _validate_journal(journal, workspace)
    journal["state"] = "prepared"
    _write_journal(workspace, journal)
    for operation in reversed(journal["operations"]):
        phase = operation["phase"]
        destination = operation["dest"]
        backup = operation["backup"]
        stage = operation["stage"]
        destination_existed = operation["dest_existed"]
        parent = Path(destination).parent
        if phase == "planned":
            continue
        if phase == "backup_moving":
            if os.path.lexists(backup):
                if os.path.lexists(destination):
                    raise TomlinksException(f"ambiguous recovery state for {destination}")
                os.replace(backup, destination)
                _fsync_directory(parent)
            elif destination_existed and not os.path.lexists(destination):
                raise TomlinksException(f"missing backup during recovery: {backup}")
            operation["phase"] = "planned"
            _write_journal(workspace, journal)
            continue
        if phase == "installing":
            if os.path.lexists(stage):
                operation["phase"] = "backup_moved"
                _write_journal(workspace, journal)
                phase = "backup_moved"
            else:
                operation["phase"] = "installed"
                _write_journal(workspace, journal)
                phase = "installed"
        if phase == "installed":
            if os.path.lexists(destination):
                _remove_path(destination)
                _fsync_directory(parent)
            if destination_existed:
                if not os.path.lexists(backup):
                    raise TomlinksException(f"missing backup during recovery: {backup}")
                os.replace(backup, destination)
                _fsync_directory(parent)
            elif os.path.lexists(backup):
                raise TomlinksException(f"unexpected backup during recovery: {backup}")
            operation["phase"] = "planned"
            _write_journal(workspace, journal)
        elif phase == "backup_moved":
            if destination_existed:
                if not os.path.lexists(backup):
                    raise TomlinksException(f"missing backup during recovery: {backup}")
                if os.path.lexists(destination):
                    raise TomlinksException(f"ambiguous recovery state for {destination}")
                os.replace(backup, destination)
                _fsync_directory(parent)
            elif os.path.lexists(backup):
                raise TomlinksException(f"unexpected backup during recovery: {backup}")
            operation["phase"] = "planned"
            _write_journal(workspace, journal)
    _remove_created_parents(journal)
    _write_journal(workspace, journal)


def _remove_workspace(journal):
    workspace = Path(journal["workspace"])
    journal_path = workspace / JOURNAL_NAME
    temporary = workspace / (JOURNAL_NAME + ".tmp")
    for path in (temporary, journal_path):
        if os.path.lexists(path):
            _remove_path(path)
    try:
        os.rmdir(workspace)
    except OSError as exc:
        raise TomlinksException(f"cannot remove transaction workspace {workspace}: {exc}") from exc
    _fsync_directory(workspace.parent)


def _cleanup_transaction(journal):
    _validate_journal(journal, Path(journal["workspace"]))
    for operation in journal["operations"]:
        _remove_path(operation["stage"])
        _remove_path(operation["backup"])
    _remove_workspace(journal)


def _recover_transactions():
    try:
        TRANSACTIONS_ROOT.mkdir(parents=True, exist_ok=True)
        entries = sorted(TRANSACTIONS_ROOT.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TomlinksException(f"cannot access transaction directory: {exc}") from exc
    for workspace in entries:
        if not workspace.is_dir():
            raise TomlinksException(f"malformed transaction workspace: {workspace}")
        journal = _load_journal(workspace)
        journal["workspace"] = str(workspace)
        if journal["state"] in {"prepared", "committing"}:
            rollback_transaction(journal)
            _cleanup_transaction(journal)
        else:
            _cleanup_transaction(journal)


def _new_journal(operations, created_parents, workspace):
    txid = workspace.name
    records = []
    for index, operation in enumerate(operations):
        destination = operation["dest"]
        parent = Path(destination).parent
        records.append(
            {
                "src": operation["src"],
                "dest": destination,
                "stage": str(parent / f".tomlinks-stage-{txid}-{index}"),
                "backup": str(parent / f".tomlinks-backup-{txid}-{index}"),
                "dest_existed": os.path.lexists(destination),
                "phase": "planned",
            }
        )
    return {
        "version": 1,
        "state": "prepared",
        "workspace": str(workspace),
        "created_parents": created_parents,
        "operations": records,
    }


def _run_transaction_locked(operations, created_parents):
    if not operations:
        return
    try:
        TRANSACTIONS_ROOT.mkdir(parents=True, exist_ok=True)
        workspace = TRANSACTIONS_ROOT / uuid.uuid4().hex
        workspace.mkdir()
        journal = _new_journal(operations, created_parents, workspace)
        _validate_journal(journal, workspace)
        _write_journal(workspace, journal)
        for parent in created_parents:
            os.makedirs(parent, exist_ok=True)
            if not os.path.isdir(parent):
                raise TomlinksException(f"destination parent is not a directory: {parent}")
            _fsync_directory(Path(parent).parent)
        for operation in journal["operations"]:
            stage = operation["stage"]
            source = operation["src"]
            if os.path.lexists(stage) or os.path.lexists(operation["backup"]):
                raise TomlinksException(f"transaction staging path already exists: {stage}")
            if os.path.isdir(source):
                shutil.copytree(source, stage)
            elif os.path.isfile(source):
                shutil.copy2(source, stage)
            else:
                raise TomlinksException(f"unsupported source type: {source}")
            _fsync_tree(stage)
            _fsync_directory(Path(stage).parent)
        journal["state"] = "committing"
        _write_journal(workspace, journal)
        for operation in journal["operations"]:
            destination = operation["dest"]
            parent = Path(destination).parent
            backup = operation["backup"]
            stage = operation["stage"]
            if operation["dest_existed"]:
                operation["phase"] = "backup_moving"
                _write_journal(workspace, journal)
                if not os.path.lexists(destination):
                    raise TomlinksException(f"destination changed before commit: {destination}")
                os.replace(destination, backup)
                _fsync_directory(parent)
            operation["phase"] = "backup_moved"
            _write_journal(workspace, journal)
            operation["phase"] = "installing"
            _write_journal(workspace, journal)
            if not os.path.lexists(stage):
                raise TomlinksException(f"missing staged artifact: {stage}")
            os.replace(stage, destination)
            _fsync_directory(parent)
            operation["phase"] = "installed"
            _write_journal(workspace, journal)
        journal["state"] = "committed"
        _write_journal(workspace, journal)
        try:
            _cleanup_transaction(journal)
        except Exception as cleanup_error:
            raise TomlinksException(
                f"transaction committed but cleanup failed; manual recovery required: {cleanup_error}"
            ) from cleanup_error
    except Exception as exc:
        if "journal" not in locals():
            if isinstance(exc, TomlinksException):
                raise
            raise TomlinksException(str(exc)) from exc
        if journal.get("state") == "committed":
            raise
        try:
            rollback_transaction(journal)
            _cleanup_transaction(journal)
        except Exception as rollback_error:
            raise TomlinksException(
                f"transaction failed: {exc}; manual recovery required: {rollback_error}"
            ) from exc
        if isinstance(exc, TomlinksException):
            raise
        raise TomlinksException(str(exc)) from exc


def run_transaction(operations, created_parents=None):
    lock_stream = _lock()
    try:
        _recover_transactions()
        _run_transaction_locked(operations, created_parents or [])
    finally:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()


def _execute(mode, package_paths):
    lock_stream = _lock()
    try:
        _recover_transactions()
        operations, created_parents = _plan(mode, package_paths)
        _run_transaction_locked(operations, created_parents)
    finally:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()


def restore(*paths):
    _execute("restore", paths)


def collect(*paths):
    _execute("collect", paths)


def print_help():
    print(f"""Usage:
  tomlinks restore <package>...
  tomlinks collect <package>...
  tomlinks --help

Commands:
  restore             Copy package files to their system destinations.
  collect             Copy system files back into the package.

Each command applies all mappings transactionally and recovers interrupted transactions on the next invocation.

Version: {VERSION}
""")


def main(args):
    if not args:
        print_help()
        return 0
    if "--help" in args or "help" in args:
        print_help()
        return 0
    command = {"restore": restore, "collect": collect}.get(args[0])
    if command is None:
        print("command wasn't specified; use --help for usage information")
        return 1
    try:
        command(*args[1:])
    except (TomlinksException, OSError, configparser.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
