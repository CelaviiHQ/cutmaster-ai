#!/usr/bin/env python3
"""Celavii-Resolve — Universal Installer

Auto-detects platform, finds DaVinci Resolve, creates a virtual environment,
installs dependencies, and configures MCP clients.

Usage:
    python install.py                          # Interactive
    python install.py --clients all            # Configure all detected clients
    python install.py --clients claude-code,cursor  # Specific clients
    python install.py --clients manual         # Print config snippets only
    python install.py --dry-run                # Preview without writing
    python install.py --no-venv                # Skip venv creation
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
SERVER_NAME = "celavii-resolve"
PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
SERVER_MODULE = "celavii_resolve"
SYSTEM = platform.system()


def home() -> Path:
    return Path.home()


def appdata() -> Path:
    return Path(os.environ.get("APPDATA", home() / "AppData" / "Roaming"))


def xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", home() / ".config"))


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def platform_name() -> str:
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(SYSTEM, SYSTEM)


def find_resolve_api_path() -> str | None:
    """Find the Resolve Scripting API Modules directory."""
    paths: dict[str, list[str]] = {
        "Darwin": [
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting/Modules",
            str(home() / "Library/Application Support/Blackmagic Design/"
                "DaVinci Resolve/Developer/Scripting/Modules"),
        ],
        "Windows": [
            os.path.expandvars(
                r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve"
                r"\Support\Developer\Scripting\Modules"
            ),
        ],
        "Linux": [
            "/opt/resolve/Developer/Scripting/Modules",
            "/opt/resolve/libs/Fusion/Developer/Scripting/Modules",
        ],
    }
    for p in paths.get(SYSTEM, paths["Linux"]):
        if os.path.isdir(p):
            return p
    return None


def find_python() -> str | None:
    """Find a suitable Python 3.11+ interpreter."""
    candidates = ["python3.12", "python3.11", "python3", "python"]
    for name in candidates:
        path = shutil.which(name)
        if path:
            try:
                result = subprocess.run(
                    [path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                    capture_output=True, text=True, timeout=5,
                )
                version = result.stdout.strip()
                major, minor = map(int, version.split("."))
                if major == 3 and minor >= 11:
                    return path
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# MCP Client definitions
# ---------------------------------------------------------------------------

MCP_CLIENTS = [
    {
        "id": "claude-desktop",
        "name": "Claude Desktop",
        "get_path": lambda: {
            "Darwin": home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            "Windows": appdata() / "Claude" / "claude_desktop_config.json",
            "Linux": xdg_config() / "Claude" / "claude_desktop_config.json",
        }.get(SYSTEM),
        "config_key": "mcpServers",
    },
    {
        "id": "claude-code",
        "name": "Claude Code",
        "get_path": lambda: PROJECT_DIR / ".mcp.json",
        "config_key": "mcpServers",
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "get_path": lambda: home() / ".cursor" / "mcp.json",
        "config_key": "mcpServers",
    },
    {
        "id": "vscode",
        "name": "VS Code (Copilot)",
        "get_path": lambda: PROJECT_DIR / ".vscode" / "mcp.json",
        "config_key": "servers",
    },
    {
        "id": "windsurf",
        "name": "Windsurf",
        "get_path": lambda: {
            "Darwin": home() / ".codeium" / "windsurf" / "mcp_config.json",
            "Windows": home() / ".codeium" / "windsurf" / "mcp_config.json",
            "Linux": home() / ".codeium" / "windsurf" / "mcp_config.json",
        }.get(SYSTEM),
        "config_key": "mcpServers",
    },
    {
        "id": "cline",
        "name": "Cline",
        "get_path": lambda: None,  # VS Code global storage — varies
        "config_key": "mcpServers",
    },
    {
        "id": "roo-code",
        "name": "Roo Code",
        "get_path": lambda: None,  # VS Code global storage — varies
        "config_key": "mcpServers",
    },
    {
        "id": "zed",
        "name": "Zed",
        "get_path": lambda: {
            "Darwin": home() / ".config" / "zed" / "settings.json",
            "Linux": home() / ".config" / "zed" / "settings.json",
        }.get(SYSTEM),
        "config_key": "context_servers",
        "format": "zed",
    },
    {
        "id": "continue",
        "name": "Continue",
        "get_path": lambda: home() / ".continue" / "config.json",
        "config_key": "mcpServers",
    },
    {
        "id": "jetbrains",
        "name": "JetBrains IDEs",
        "get_path": lambda: None,  # Manual — Settings > Tools > AI Assistant > MCP
        "config_key": "mcpServers",
    },
]


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

def build_server_entry(python_path: str, server_dir: str) -> dict:
    """Standard MCP server config entry."""
    return {
        "command": python_path,
        "args": ["-m", SERVER_MODULE],
        "cwd": server_dir,
    }


def build_zed_entry(python_path: str, server_dir: str) -> dict:
    """Zed-specific server config entry."""
    return {
        "command": {
            "path": python_path,
            "args": ["-m", SERVER_MODULE],
            "env": {"PYTHONPATH": str(SRC_DIR)},
        },
        "settings": {},
    }


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict:
    """Read a JSON file, returning empty dict if missing/invalid."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, data: dict, dry_run: bool = False) -> bool:
    """Write JSON with backup. Returns True on success."""
    if dry_run:
        print(f"  [DRY RUN] Would write to {path}")
        print(textwrap.indent(json.dumps(data, indent=2), "    "))
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Backup existing
        if path.is_file():
            backup = path.with_suffix(path.suffix + ".backup")
            shutil.copy2(path, backup)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return True
    except OSError as exc:
        print(f"  ERROR: Failed to write {path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Venv setup
# ---------------------------------------------------------------------------

def setup_venv(python_path: str, dry_run: bool = False) -> str | None:
    """Create a virtual environment and install dependencies.

    Returns the path to the venv Python interpreter, or None on failure.
    """
    venv_dir = PROJECT_DIR / ".venv"
    venv_python = venv_dir / ("Scripts" / "python.exe" if SYSTEM == "Windows" else "bin" / "python3")

    if venv_dir.is_dir() and venv_python.is_file():
        print(f"  Virtual environment already exists at {venv_dir}")
        return str(venv_python)

    if dry_run:
        print(f"  [DRY RUN] Would create venv at {venv_dir}")
        return str(venv_python)

    print(f"  Creating virtual environment at {venv_dir}...")
    try:
        subprocess.run([python_path, "-m", "venv", str(venv_dir)], check=True)
    except subprocess.CalledProcessError:
        print("  ERROR: Failed to create virtual environment.")
        return None

    print("  Installing dependencies...")
    try:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-q", "-e", f"{PROJECT_DIR}[dev]"],
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ERROR: Failed to install dependencies.")
        return None

    print("  Dependencies installed successfully.")
    return str(venv_python)


# ---------------------------------------------------------------------------
# Resolve connection verification
# ---------------------------------------------------------------------------

def verify_resolve_connection(python_path: str) -> tuple[bool, str]:
    """Verify DaVinciResolveScript can be imported."""
    api_path = find_resolve_api_path()
    if not api_path:
        return False, "Resolve API path not found"

    test_script = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, {api_path!r})
        try:
            import DaVinciResolveScript as dvr
            resolve = dvr.scriptapp('Resolve')
            if resolve:
                name = resolve.GetProductName()
                ver = resolve.GetVersionString()
                print(f"CONNECTED: {{name}} {{ver}}")
            else:
                print("IMPORTED: API module loads but Resolve not running")
        except ImportError as e:
            print(f"IMPORT_ERROR: {{e}}")
        except Exception as e:
            print(f"ERROR: {{e}}")
    """)

    try:
        result = subprocess.run(
            [python_path, "-c", test_script],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if output.startswith("CONNECTED:"):
            return True, output.replace("CONNECTED: ", "")
        elif output.startswith("IMPORTED:"):
            return True, "API module loaded (Resolve not running)"
        else:
            return False, output or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Client configuration
# ---------------------------------------------------------------------------

def configure_client(client: dict, venv_python: str, dry_run: bool = False) -> tuple[bool, str]:
    """Write MCP config for a specific client. Returns (success, message)."""
    config_path = client["get_path"]()
    if config_path is None:
        return False, f"{client['name']} — path not available on {platform_name()}"

    config_key = client["config_key"]
    is_zed = client.get("format") == "zed"

    if is_zed:
        server_entry = build_zed_entry(venv_python, str(PROJECT_DIR))
    else:
        server_entry = build_server_entry(venv_python, str(PROJECT_DIR))

    if dry_run:
        preview = {config_key: {SERVER_NAME: server_entry}}
        print(f"  [DRY RUN] {client['name']} -> {config_path}")
        print(textwrap.indent(json.dumps(preview, indent=2), "    "))
        return True, str(config_path)

    existing = read_json(config_path)
    if config_key not in existing:
        existing[config_key] = {}
    existing[config_key][SERVER_NAME] = server_entry

    if write_json(config_path, existing):
        return True, str(config_path)
    return False, f"Failed to write {config_path}"


def print_manual_config(venv_python: str):
    """Print config snippets for manual setup."""
    entry = build_server_entry(venv_python, str(PROJECT_DIR))
    zed_entry = build_zed_entry(venv_python, str(PROJECT_DIR))

    print("\n--- Standard MCP Config (Claude Desktop, Cursor, Windsurf, etc.) ---")
    print(json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2))

    print("\n--- VS Code (Copilot) ---")
    print(json.dumps({"servers": {SERVER_NAME: entry}}, indent=2))

    print("\n--- Zed ---")
    print(json.dumps({"context_servers": {SERVER_NAME: zed_entry}}, indent=2))

    print("\n--- JetBrains IDEs ---")
    print("Settings > Tools > AI Assistant > MCP Servers > Add")
    print(f"  Command: {venv_python}")
    print(f"  Args: -m {SERVER_MODULE}")
    print(f"  Working dir: {PROJECT_DIR}")


# ---------------------------------------------------------------------------
# Main installer flow
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Celavii-Resolve — Universal MCP Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--clients",
        default="",
        help="Comma-separated client IDs (e.g. 'claude-desktop,cursor'), "
             "'all' for all detected, or 'manual' for config snippets",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--no-venv", action="store_true", help="Skip virtual environment creation")
    parser.add_argument("--verify", action="store_true", help="Only verify Resolve connection")
    args = parser.parse_args()

    print(f"\n  Celavii-Resolve Installer v{VERSION}")
    print(f"  Platform: {platform_name()}")
    print(f"  Project:  {PROJECT_DIR}\n")

    # 1. Find Python
    python_path = find_python()
    if not python_path:
        print("  ERROR: Python 3.11+ required but not found.")
        print("  Install Python 3.11 or 3.12 from python.org")
        sys.exit(1)
    print(f"  Python: {python_path}")

    # 2. Check Resolve API
    api_path = find_resolve_api_path()
    if api_path:
        print(f"  Resolve API: {api_path}")
    else:
        print("  Resolve API: NOT FOUND")
        print("  DaVinci Resolve Studio must be installed for the API to work.")
        print("  The server will still install but won't connect until Resolve is available.")

    # 3. Verify connection
    if api_path:
        ok, msg = verify_resolve_connection(python_path)
        print(f"  Connection: {msg}")
    else:
        ok = False

    if args.verify:
        sys.exit(0 if ok else 1)

    # 4. Set up venv
    if args.no_venv:
        venv_python = python_path
        print("\n  Skipping venv creation (--no-venv)")
    else:
        print("\n  Setting up virtual environment...")
        venv_python = setup_venv(python_path, args.dry_run)
        if not venv_python:
            print("  ERROR: Venv setup failed. Try --no-venv to skip.")
            sys.exit(1)
        print(f"  Venv Python: {venv_python}")

    # 5. Configure clients
    if args.clients == "manual":
        print_manual_config(venv_python)
        return

    if args.clients == "all":
        selected = [c for c in MCP_CLIENTS if c["get_path"]() is not None]
    elif args.clients:
        ids = [s.strip() for s in args.clients.split(",")]
        selected = [c for c in MCP_CLIENTS if c["id"] in ids]
        unknown = set(ids) - {c["id"] for c in selected}
        if unknown:
            print(f"  WARNING: Unknown client IDs: {', '.join(unknown)}")
            valid_ids = ", ".join(c["id"] for c in MCP_CLIENTS)
            print(f"  Valid IDs: {valid_ids}")
    else:
        # Interactive — ask which clients to configure
        print("\n  Available MCP clients:")
        available = []
        for i, c in enumerate(MCP_CLIENTS):
            path = c["get_path"]()
            status = "detected" if path and path.parent.is_dir() else "manual"
            if path is None:
                status = "not available"
            print(f"    {i + 1}. {c['name']:20s} [{status}]")
            if path is not None:
                available.append(c)

        print("\n  Enter client numbers (comma-separated), 'all', or 'manual':")
        choice = input("  > ").strip().lower()

        if choice == "all":
            selected = available
        elif choice == "manual":
            print_manual_config(venv_python)
            return
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [MCP_CLIENTS[i] for i in indices if 0 <= i < len(MCP_CLIENTS)]
            except (ValueError, IndexError):
                print("  Invalid selection.")
                sys.exit(1)

    if not selected:
        print("\n  No clients selected. Use --clients manual to get config snippets.")
        return

    print(f"\n  Configuring {len(selected)} client(s)...\n")
    results = []
    for client in selected:
        success, msg = configure_client(client, venv_python, args.dry_run)
        status = "OK" if success else "FAILED"
        print(f"    {client['name']:20s} [{status}] {msg}")
        results.append((client["name"], success))

    # 6. Summary
    ok_count = sum(1 for _, s in results if s)
    fail_count = len(results) - ok_count
    print(f"\n  Done! {ok_count} client(s) configured, {fail_count} failed.")

    if api_path:
        print("\n  Resolve API found. Server ready to connect.")
    else:
        print("\n  Install DaVinci Resolve Studio for full functionality.")

    print(f"  Start the server: {venv_python} -m {SERVER_MODULE}")
    print()


if __name__ == "__main__":
    main()
