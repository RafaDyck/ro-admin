"""Fail if the built wheel is missing any file of the web UI.

The UI ships as package data. A file added under src/ro_admin/web/ that no
pattern in pyproject.toml matches is served in development, where the source
tree is on disk, and silently absent from every install. The result would be a
blank page or a module that 404s, found by an operator rather than by CI.

    pip wheel . --no-deps -w dist
    python scripts/check_wheel_ships_web.py dist
"""
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "ro_admin" / "web"


def missing_from(wheel: pathlib.Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        shipped = set(archive.namelist())
    expected = sorted(
        "ro_admin/web/" + p.relative_to(WEB).as_posix()
        for p in WEB.rglob("*") if p.is_file()
    )
    return [name for name in expected if name not in shipped]


def main(dist: str) -> int:
    wheels = sorted(pathlib.Path(dist).glob("ro_admin-*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one ro_admin wheel in {dist}, found {len(wheels)}")
        return 2
    missing = missing_from(wheels[0])
    if missing:
        print(f"{len(missing)} web UI file(s) are not in {wheels[0].name}:\n")
        for name in missing:
            print(f"  {name}")
        print(
            "\nAdd a matching pattern to [tool.setuptools.package-data] in"
            " pyproject.toml."
        )
        return 1
    print(f"every web UI file is in {wheels[0].name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist"))
