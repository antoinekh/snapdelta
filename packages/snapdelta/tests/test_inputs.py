from snapdelta.inputs import InputFile


def test_sha256_and_from_path(tmp_path) -> None:
    path = tmp_path / "a.xml"
    path.write_bytes(b"<configuration/>")
    file = InputFile.from_path(path)
    assert file.name == "a.xml"
    assert len(file.sha256) == 64
