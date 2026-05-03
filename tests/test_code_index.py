"""Tests for code index — file outline scanning and symbol search."""

from wisp.code_index import (
    Symbol,
    CodeIndex,
    build_index,
    search_symbols,
    format_index_summary,
)


def test_empty_directory(tmp_path):
    """Empty directory yields empty index."""
    index = build_index(str(tmp_path))
    assert index.total_symbols == 0
    assert index.files_scanned == 0
    assert format_index_summary(index) == ""


def test_python_symbols(tmp_path):
    """Detect Python classes and functions."""
    src = tmp_path / "main.py"
    src.write_text(
        "class UserModel:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "    \n"
        "    def get_name(self):\n"
        "        return self.name\n"
        "\n"
        "def create_user(name):\n"
        "    return UserModel(name)\n"
        "\n"
        "async def fetch_users():\n"
        "    pass\n"
    )
    index = build_index(str(tmp_path))
    assert index.total_symbols >= 4
    assert index.languages == {"Python"}

    # Check specific symbols
    symbols = index.symbols.get("main.py", [])
    names = [(s.name, s.kind) for s in symbols]
    assert ("UserModel", "class") in names
    assert ("__init__", "method") in names
    assert ("get_name", "method") in names
    assert ("create_user", "function") in names
    assert ("fetch_users", "function") in names


def test_rust_symbols(tmp_path):
    """Detect Rust structs, functions, traits, enums."""
    src = tmp_path / "lib.rs"
    src.write_text(
        "pub struct Config {\n"
        "    pub name: String,\n"
        "}\n"
        "\n"
        "pub enum Status {\n"
        "    Active,\n"
        "    Inactive,\n"
        "}\n"
        "\n"
        "pub trait Runnable {\n"
        "    fn run(&self);\n"
        "}\n"
        "\n"
        "pub fn process(data: &str) -> bool {\n"
        "    true\n"
        "}\n"
        "\n"
        "impl Runnable for Config {\n"
        "    fn run(&self) {}\n"
        "}\n"
    )
    index = build_index(str(tmp_path))
    symbols = index.symbols.get("lib.rs", [])
    names = [(s.name, s.kind) for s in symbols]
    assert ("Config", "struct") in names
    assert ("Status", "enum") in names
    assert ("Runnable", "trait") in names
    assert ("process", "function") in names
    assert ("impl Runnable for Config", "impl") in names
    assert ("run", "method") in names


def test_javascript_symbols(tmp_path):
    """Detect JavaScript functions and classes."""
    src = tmp_path / "app.js"
    src.write_text(
        "class UserController {\n"
        "    constructor(name) {\n"
        "        this.name = name\n"
        "    }\n"
        "    \n"
        "    getName() {\n"
        "        return this.name\n"
        "    }\n"
        "}\n"
        "\n"
        "function formatDate(date) {\n"
        "    return date.toString()\n"
        "}\n"
        "\n"
        "const helper = (x) => x * 2;\n"
    )
    index = build_index(str(tmp_path))
    symbols = index.symbols.get("app.js", [])
    names = [(s.name, s.kind) for s in symbols]
    assert ("UserController", "class") in names
    assert ("getName", "method") in names
    assert ("formatDate", "function") in names
    assert ("helper", "function") in names


def test_go_symbols(tmp_path):
    """Detect Go functions and structs."""
    src = tmp_path / "main.go"
    src.write_text(
        "package main\n"
        "\n"
        "type Config struct {\n"
        "    Name string\n"
        "}\n"
        "\n"
        "type Handler interface {\n"
        "    ServeHTTP()\n"
        "}\n"
        "\n"
        "func main() {\n"
        "}\n"
        "\n"
        "func (c *Config) Load() error {\n"
        "    return nil\n"
        "}\n"
    )
    index = build_index(str(tmp_path))
    symbols = index.symbols.get("main.go", [])
    names = [(s.name, s.kind) for s in symbols]
    assert ("Config", "struct") in names
    assert ("Handler", "interface") in names
    assert ("main", "function") in names


def test_search_by_name(tmp_path):
    """Search finds symbols by name."""
    src = tmp_path / "utils.py"
    src.write_text(
        "def validate_email(email): pass\n"
        "def validate_phone(phone): pass\n"
        "def format_date(date): pass\n"
    )
    index = build_index(str(tmp_path))
    results = search_symbols(index, "validate")
    assert len(results) == 2
    assert all("validate" in r.name for r in results)


def test_search_by_kind(tmp_path):
    """Search finds symbols by kind."""
    src = tmp_path / "models.py"
    src.write_text(
        "class User: pass\n"
        "class Admin: pass\n"
        "def helper(): pass\n"
    )
    index = build_index(str(tmp_path))
    results = search_symbols(index, "class")
    assert len(results) == 2
    assert all(r.kind == "class" for r in results)


def test_search_max_results(tmp_path):
    """Search respects max_results limit."""
    src = tmp_path / "many.py"
    src.write_text("\n".join(f"def func_{i}(): pass" for i in range(50)))
    index = build_index(str(tmp_path))
    results = search_symbols(index, "func", max_results=5)
    assert len(results) == 5


def test_format_index_summary():
    """Format index summary for system prompt."""
    index = CodeIndex(
        symbols={
            "main.py": [
                Symbol(name="run", kind="function", file="main.py", line=1),
                Symbol(name="Config", kind="class", file="main.py", line=5),
            ],
        },
        files_scanned=1,
        total_symbols=2,
        languages={"Python"},
    )
    summary = format_index_summary(index)
    assert "2 symbols" in summary
    assert "1 files" in summary
    assert "Python" in summary
    assert "search_symbols()" in summary


def test_ignores_non_source_files(tmp_path):
    """Non-source files are ignored."""
    (tmp_path / "data.json").write_text('{"key": "value"}')
    (tmp_path / "config.yaml").write_text("key: value")
    index = build_index(str(tmp_path))
    assert index.total_symbols == 0


def test_ignores_hidden_directories(tmp_path):
    """Files in hidden directories are skipped."""
    hidden = tmp_path / ".hidden_dir"
    hidden.mkdir()
    (hidden / "main.py").write_text("def hidden_func(): pass")
    index = build_index(str(tmp_path))
    assert index.total_symbols == 0
