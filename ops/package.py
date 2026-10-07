"""Build a source handoff archive from an explicit allowlist; never include secrets or databases."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

root=Path(__file__).resolve().parents[1]
files=["README.md","pyproject.toml","uv.lock","Dockerfile","compose.yaml","compose.local.yaml",
       "compose.prod.yaml",".env.example",".gitignore",".gitattributes",".dockerignore"]
directories=["src","dbt","config","migrations","demo","tests","ops","docs",".github"]
with ZipFile(root/"dqflow-source.zip","w",ZIP_DEFLATED,compresslevel=6) as archive:
    for filename in files:
        archive.write(root/filename,"dqflow/"+filename)
    for directory in directories:
        for path in sorted((root/directory).rglob("*")):
            if path.is_file() and not any(part in ("__pycache__","logs","target") for part in path.parts) and path.suffix not in (".pyc",".log"):
                name = "dqflow/" + path.relative_to(root).as_posix()
                if path.suffix == ".sh":
                    info = ZipInfo.from_file(path, name)
                    info.create_system = 3
                    info.external_attr = 0o100755 << 16
                    archive.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"), compress_type=ZIP_DEFLATED)
                else:
                    archive.write(path, name)
print(root/"dqflow-source.zip")
