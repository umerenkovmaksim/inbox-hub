import hashlib

from university_pz_parser.storage.files import FileStore


def test_put_moves_and_dedups(tmp_path):
    store = FileStore(tmp_path / "store")
    a = store.new_tmp_dir() / "a.PDF"
    a.write_bytes(b"same")
    b = store.new_tmp_dir() / "b.pdf"
    b.write_bytes(b"same")
    first = store.put(a, "Лекция.PDF")
    second = store.put(b, None)
    assert first.sha256 == hashlib.sha256(b"same").hexdigest()
    assert first.path == second.path
    assert first.path.suffix == ".pdf"
    assert first.path.read_bytes() == b"same"
    assert not a.exists() and not b.exists()
    assert store.resolve(str(first.path)) == first.path
