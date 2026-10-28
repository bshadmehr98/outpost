import json

from outpost.needs import parsers


def test_requirements_with_includes_and_noise(tmp_path):
    (tmp_path / "base.txt").write_text(
        "django>=4.2  # web\n\n# comment\n-r extra.txt\n--index-url https://x\n./local-pkg\ngit+https://x/y.git\n"
    )
    (tmp_path / "extra.txt").write_text("psycopg[binary]==3.2.3\n-r base.txt\n")
    assert parsers.parse_requirements(tmp_path / "base.txt") == ["django>=4.2", "psycopg[binary]==3.2.3"]


def test_poetry_lock(tmp_path):
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "flask"\nversion = "3.0.0"\n\n[[package]]\nname = "mine"\nversion = "0.1"\n[package.source]\ntype = "directory"\n'
    )
    assert parsers.parse_python_file(tmp_path / "poetry.lock") == ["flask==3.0.0"]


def test_uv_lock(tmp_path):
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "my-app"\nversion = "0.1.0"\nsource = { editable = "." }\n\n'
        '[[package]]\nname = "httpx"\nversion = "0.27.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    )
    assert parsers.parse_python_file(tmp_path / "uv.lock") == ["httpx==0.27.0"]


def test_pipfile_lock(tmp_path):
    (tmp_path / "Pipfile.lock").write_text(
        json.dumps({"default": {"requests": {"version": "==2.31.0"}}, "develop": {"pytest": {"version": "==8.0.0"}}})
    )
    assert parsers.parse_python_file(tmp_path / "Pipfile.lock") == ["requests==2.31.0", "pytest==8.0.0"]


def test_package_lock_v3(tmp_path):
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "app", "version": "1.0.0"},
                    "node_modules/chalk": {
                        "version": "5.3.0",
                        "resolved": "https://registry.npmjs.org/chalk/-/chalk-5.3.0.tgz",
                        "integrity": "sha512-abc",
                    },
                    "node_modules/@scope/pkg": {
                        "version": "1.2.3",
                        "resolved": "https://registry.npmjs.org/@scope/pkg/-/pkg-1.2.3.tgz",
                    },
                    "node_modules/chalk/node_modules/ansi": {
                        "version": "6.0.0",
                        "resolved": "https://registry.npmjs.org/ansi/-/ansi-6.0.0.tgz",
                    },
                    "node_modules/linked": {"link": True, "resolved": "../linked"},
                    "packages/local": {"version": "0.0.1"},
                },
            }
        )
    )
    needs = parsers.parse_js_file(tmp_path / "package-lock.json")
    assert [(n.name, n.version) for n in needs] == [("chalk", "5.3.0"), ("@scope/pkg", "1.2.3"), ("ansi", "6.0.0")]
    assert needs[0].integrity == "sha512-abc"


def test_package_lock_v1(tmp_path):
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {
                    "a": {
                        "version": "1.0.0",
                        "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz",
                        "dependencies": {"b": {"version": "2.0.0", "resolved": "https://r/b.tgz"}},
                    }
                },
            }
        )
    )
    assert [(n.name, n.version) for n in parsers.parse_package_lock(tmp_path / "package-lock.json")] == [
        ("a", "1.0.0"),
        ("b", "2.0.0"),
    ]


def test_pnpm_lock(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '9.0'\npackages:\n  chalk@5.3.0:\n    resolution: {integrity: sha512-xyz}\n"
        "  '@types/node@20.1.0':\n    resolution: {integrity: sha512-q}\n  /old-style@1.0.0:\n    resolution: {integrity: sha512-o}\n"
        "  local@1.0.0:\n    resolution: {tarball: file:local.tgz}\n"
    )
    needs = parsers.parse_js_file(tmp_path / "pnpm-lock.yaml")
    assert [(n.name, n.version) for n in needs] == [
        ("chalk", "5.3.0"),
        ("@types/node", "20.1.0"),
        ("old-style", "1.0.0"),
    ]


def test_yarn_lock(tmp_path):
    (tmp_path / "yarn.lock").write_text(
        '# yarn lockfile v1\n\n\n"@babel/core@^7.0.0", "@babel/core@^7.1.0":\n  version "7.23.0"\n  resolved "https://registry.yarnpkg.com/@babel/core/-/core-7.23.0.tgz#abc"\n  integrity sha512-aaa\n\n'
        'chalk@^4.0.0:\n  version "4.1.2"\n  resolved "https://registry.yarnpkg.com/chalk/-/chalk-4.1.2.tgz#def"\n  integrity sha512-bbb\n'
    )
    needs = parsers.parse_js_file(tmp_path / "yarn.lock")
    assert [(n.name, n.version, n.resolved) for n in needs] == [
        ("@babel/core", "7.23.0", "https://registry.yarnpkg.com/@babel/core/-/core-7.23.0.tgz"),
        ("chalk", "4.1.2", "https://registry.yarnpkg.com/chalk/-/chalk-4.1.2.tgz"),
    ]
