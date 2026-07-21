#!/usr/bin/env python3
"""
opencodex - CLI for OpenCodeX Proxy + Codex integration

Commands:
  start          Start proxy + restart Codex with proxy profile
  stop           Stop proxy + restore original Codex config
  restart        Stop then start
  status         Show proxy and Codex status
  config         Open TUI config editor
  models         List available models from proxy
  discover       Discover models from a URL
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# A PyInstaller one-file executable extracts its bundled files to a temporary
# directory. Runtime files must instead live beside the executable so they
# remain available to the proxy process after startup.
FROZEN = bool(getattr(sys, "frozen", False))
PROXY_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent
# PyInstaller extracts bundled read-only resources here; runtime files still
# live beside the executable in PROXY_DIR.
BUNDLED_DIR = Path(getattr(sys, "_MEIPASS", PROXY_DIR))
SRC_DIR = PROXY_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
PID_FILE = PROXY_DIR / "proxy.pid"
LOG_FILE = PROXY_DIR / "proxy.log"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CODEX_BACKUP = PROXY_DIR / "backup" / "config.original.toml"
CONFIG_FILE = Path.home() / ".config" / "opencodex-proxy" / "config.json"

ANSI_BOLD = "\033[1m"
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"

CODEX_PROXY_PROVIDER = """
[model_providers.opencodex]
name = "OpenCodeX"
base_url = "http://127.0.0.1:8787/v1"
experimental_bearer_token = "any-string-here"
wire_api = "responses"
"""

# Top-level setting that tells Codex to load our full-format model catalog.
# Without this, Codex never reads the catalog and only shows built-in models.
MODEL_CATALOG_JSON_LINE = (
    'model_catalog_json = "~/.codex/model-catalogs/opencodex.json"'
)

MODEL_CATALOG_DIR = Path.home() / ".codex" / "model-catalogs"
MODEL_CATALOG_FILE = MODEL_CATALOG_DIR / "opencodex.json"


def trace(msg: str) -> None:
    print(f"  {msg}")


def is_proxy_running() -> bool:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True, timeout=5,
                )
                return str(pid) in result.stdout
            else:
                os.kill(pid, 0)
                return True
        except Exception:
            pass
    return False


def get_proxy_pid() -> int | None:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            pass
    return None


def port_in_use(port: int = 8787) -> bool:
    """Return True if anything is already listening on 127.0.0.1:<port>."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pids_on_port(port: int = 8787) -> set[int]:
    """Return all PIDs that have a listening socket on 127.0.0.1:<port> (Windows)."""
    pids: set[int] = set()
    if sys.platform != "win32":
        return pids
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return pids
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        state = parts[3]
        if local.endswith(f":{port}") and state.upper() == "LISTENING":
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    return pids


def stop_proxy() -> None:
    # Kill every process bound to the proxy port (handles stacked/orphaned launches).
    killed: set[int] = set()
    for pid in pids_on_port():
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            killed.add(pid)
        except Exception:
            pass
    # Also honor the pid-file entry in case it differs from what netstat reported.
    pid = get_proxy_pid()
    if pid and pid not in killed:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    PID_FILE.unlink(missing_ok=True)


def backup_codex_config() -> None:
    if CODEX_CONFIG.exists() and not CODEX_BACKUP.exists():
        CODEX_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        CODEX_BACKUP.write_text(CODEX_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")


def restore_codex_config() -> bool:
    if CODEX_BACKUP.exists():
        CODEX_CONFIG.write_text(CODEX_BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
        CODEX_BACKUP.unlink()
        return True
    return False


def add_provider_to_config() -> bool:
    if not CODEX_CONFIG.exists():
        return False
    content = CODEX_CONFIG.read_text(encoding="utf-8")
    already_there = (
        "[model_providers.opencodex]" in content
        and MODEL_CATALOG_JSON_LINE in content
    )
    if already_there:
        return True

    backup_codex_config()
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("[marketplaces."):
            insert_idx = i
            break

    # Inject the catalog reference as a top-level key (must precede any
    # [section] so it is not swallowed into the provider table).
    if MODEL_CATALOG_JSON_LINE not in content:
        lines.insert(insert_idx, MODEL_CATALOG_JSON_LINE)
        insert_idx += 1

    if "[model_providers.opencodex]" not in content:
        for line in CODEX_PROXY_PROVIDER.strip().split("\n"):
            lines.insert(insert_idx, line)
            insert_idx += 1
    CODEX_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    return True


def remove_provider_from_config() -> None:
    if not CODEX_CONFIG.exists():
        return
    content = CODEX_CONFIG.read_text(encoding="utf-8")
    if "[model_providers.opencodex]" not in content and MODEL_CATALOG_JSON_LINE not in content:
        return
    lines = content.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.strip() == "[model_providers.opencodex]":
            skip = True
            continue
        if skip and (line.startswith("[") or line.strip() == ""):
            skip = False
        if skip:
            continue
        if line.strip() == MODEL_CATALOG_JSON_LINE:
            continue
        new_lines.append(line)
    CODEX_CONFIG.write_text("\n".join(new_lines), encoding="utf-8")


def restart_codex() -> None:
    # Kill existing Codex process first.
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "Codex.exe"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.Popen(
            "start shell:AppsFolder\\OpenAI.Codex_2p2nqsd0c76g0!App",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def update_model_catalog() -> None:
    """Refresh the model catalog while PRESERVING the exact full-format file.

    The catalog at MODEL_CATALOG_FILE carries rich per-model metadata
    (base_instructions, model_messages, supported_reasoning_levels, etc.)
    that Codex needs to display the models. We must never overwrite it with
    bare {slug, display_name} entries — that strips the metadata and the
    models stop showing. So we only reconcile the slug list against what the
    proxy currently serves, keeping every existing model's full object as-is,
    and inserting any new slugs using the bundled reference catalog entry as a
    template.
    """
    import urllib.request
    import shutil

    MODEL_CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    # Seed template: the bundled full-format reference catalog.
    template_path = BUNDLED_DIR / "contrib" / "opencodex-catalog.json"
    template_models: dict[str, dict] = {}
    base_template: dict | None = None
    if template_path.exists():
        try:
            tpl = json.loads(template_path.read_text(encoding="utf-8"))
            for m in tpl.get("models", []):
                if isinstance(m, dict) and m.get("slug"):
                    template_models[m["slug"]] = m
            if tpl.get("models"):
                base_template = dict(tpl["models"][0])
        except Exception:
            pass

    # Load existing catalog (or seed from template if missing).
    if MODEL_CATALOG_FILE.exists():
        try:
            catalog = json.loads(MODEL_CATALOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            catalog = {}
    else:
        catalog = {}

    existing_models = catalog.get("models", [])
    if not isinstance(existing_models, list):
        existing_models = []

    existing_by_slug: dict[str, dict] = {}
    for m in existing_models:
        if isinstance(m, dict) and m.get("slug"):
            existing_by_slug[m["slug"]] = m

    # Fetch the live slug list from the proxy.
    live_slugs: list[str] = []
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8787/v1/models",
            headers={"Authorization": f"Bearer {os.environ.get('OPENCODE_GO_API_KEY', '')}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        live_slugs = [m["id"] for m in data.get("data", []) if m.get("id")]
    except Exception:
        live_slugs = []

    # Load config to get display names for mapped models.
    config_data: dict = {}
    try:
        config_path = Path.home() / ".config" / "opencodex-proxy" / "config.json"
        if config_path.exists():
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    mappings = config_data.get("mappings", {})
    # Map codex_name -> "Provider:model_name" for display.
    codex_to_display: dict[str, str] = {}
    for codex_name, target in mappings.items():
        codex_to_display[codex_name] = target

    # Build the new model list: keep full-format objects for live slugs,
    # prefer existing metadata, fall back to template, finally a minimal entry.
    new_models: list[dict] = []
    for slug in live_slugs:
        if slug in existing_by_slug:
            entry = existing_by_slug[slug]
            # Update display name for mapped models.
            if slug in codex_to_display:
                entry["display_name"] = codex_to_display[slug]
            new_models.append(entry)
        elif slug in template_models:
            new_models.append(template_models[slug])
        else:
            # Unknown slug (user provider model): inherit full-format structure
            # from the template so Codex displays it, then override identifiers.
            entry = dict(base_template) if base_template else {}
            display = codex_to_display.get(slug, slug.replace("-", " ").replace("_", " ").title())
            entry.update({
                "slug": slug,
                "display_name": display,
                "description": "OpenCodeX proxy model",
                "supported_in_api": True,
                "visibility": "list",
                "context_window": 128000,
                "max_context_window": 128000,
                "input_modalities": ["text"],
            })
            new_models.append(entry)

    # Preserve top-level wrapper fields exactly; only refresh models + timestamp.
    catalog.setdefault("fetched_at", "2026-06-22T10:18:00.000000Z")
    catalog.setdefault("etag", 'W/"opencodex-catalog-v0.1.2"')
    catalog.setdefault("client_version", "0.137.0")
    catalog["models"] = new_models

    MODEL_CATALOG_FILE.write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def cmd_start(args: list[str]) -> None:
    trace(f"{ANSI_BOLD}Starting proxy...{ANSI_RESET}")
    if port_in_use():
        trace(f"{ANSI_YELLOW}Proxy already listening on 8787 — not starting a duplicate{ANSI_RESET}")
        # Still reconcile Codex config + catalog and restart Codex so the
        # OpenCodeX models show up even on a re-run.
        add_provider_to_config()
        if "--no-codex" not in args:
            trace("Updating model catalog...")
            update_model_catalog()
            trace("Restarting Codex...")
            restart_codex()
            trace(f"{ANSI_GREEN}Codex restarted{ANSI_RESET}")
        return
    if is_proxy_running():
        trace(f"{ANSI_YELLOW}Proxy PID {get_proxy_pid()} alive but port free — killing stray and relaunching{ANSI_RESET}")
        stop_proxy()
        time.sleep(1)

    env = os.environ.copy()

    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # The default/fallback upstream is OpenCode Go (the bridge's origin).
    # User providers (e.g. Ollama) are loaded by the proxy from this config file
    # and used for routing their own models; they are NOT the default upstream.
    api_key = env.get("OPENCODE_GO_API_KEY", "")
    base_url = "https://opencode.ai/zen/go/v1"

    providers = config.get("providers", {})
    if not api_key and not providers:
        trace(f"{ANSI_RED}Set OPENCODE_GO_API_KEY or add a provider in config{ANSI_RESET}")
        return

    if api_key:
        env["OPENCODE_GO_API_KEY"] = api_key

    # Make the opencodex_proxy package importable when launched as a module,
    # regardless of whether the project is pip/uv-installed. A frozen build
    # relaunches this executable in an internal proxy-server mode instead.
    env = dict(env)
    if not FROZEN:
        existing_pp = env.get("PYTHONPATH", "")
        parts = [str(SRC_DIR)] + ([existing_pp] if existing_pp else [])
        env["PYTHONPATH"] = os.pathsep.join(parts)

    add_provider_to_config()

    cmd = ([sys.executable, "--proxy-server"] if FROZEN else [
        sys.executable, "-m", "opencodex_proxy",
    ]) + [
        "--bind", "127.0.0.1",
        "--port", "8787",
        "--chat-base-url", base_url,
        "--config", str(CONFIG_FILE),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROXY_DIR),
        env=env,
        stdout=open(LOG_FILE, "w"),
        stderr=subprocess.STDOUT,
    )
    PID_FILE.write_text(str(proc.pid))
    time.sleep(3)

    if is_proxy_running():
        trace(f"{ANSI_GREEN}Proxy running (PID {proc.pid}){ANSI_RESET}")
        trace("Updating model catalog...")
        update_model_catalog()
        if "--no-codex" not in args:
            trace("Restarting Codex...")
            restart_codex()
            trace(f"{ANSI_GREEN}Codex restarted{ANSI_RESET}")
    else:
        trace(f"{ANSI_RED}Proxy failed to start. Check {LOG_FILE}{ANSI_RESET}")


def cmd_stop(args: list[str]) -> None:
    trace(f"{ANSI_BOLD}Stopping proxy...{ANSI_RESET}")
    stop_proxy()
    trace(f"{ANSI_GREEN}Proxy stopped{ANSI_RESET}")

    if "--no-codex" not in args:
        trace("Restoring original Codex config...")
        remove_provider_from_config()
        trace("Restarting Codex...")
        restart_codex()
        trace(f"{ANSI_GREEN}Codex restored and restarted{ANSI_RESET}")


def cmd_restart(args: list[str]) -> None:
    cmd_stop(["--no-codex"])
    time.sleep(1)
    cmd_start(args)


def cmd_status(args: list[str]) -> None:
    print(f"{ANSI_BOLD}Proxy Status:{ANSI_RESET}")
    if is_proxy_running():
        print(f"  {ANSI_GREEN}Running{ANSI_RESET} (PID {get_proxy_pid()})")
    else:
        print(f"  {ANSI_RED}Stopped{ANSI_RESET}")

    print(f"\n{ANSI_BOLD}Codex Config:{ANSI_RESET}")
    if CODEX_CONFIG.exists():
        content = CODEX_CONFIG.read_text(encoding="utf-8")
        has_provider = "[model_providers.opencodex]" in content
        print(f"  Provider: {ANSI_GREEN if has_provider else ANSI_YELLOW}{'Added' if has_provider else 'Not added'}{ANSI_RESET}")
    else:
        print(f"  {ANSI_RED}Config not found{ANSI_RESET}")


def cmd_models(args: list[str]) -> None:
    import urllib.request
    url = "http://127.0.0.1:8787/v1/models"
    api_key = os.environ.get("OPENCODE_GO_API_KEY", "")
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = data.get("data", [])
            if not models:
                print(f"  {ANSI_YELLOW}No models found{ANSI_RESET}")
                return
            print(f"{ANSI_BOLD}Available Models:{ANSI_RESET}")
            for m in models:
                print(f"  - {ANSI_CYAN}{m['id']}{ANSI_RESET}")
    except Exception as e:
        print(f"  {ANSI_RED}Proxy not running or error: {e}{ANSI_RESET}")


def cmd_discover(args: list[str]) -> None:
    import urllib.request
    if not args:
        print(f"  Usage: opencodex discover <url>")
        return
    url = args[0].rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            models = data.get("data", [])
            print(f"{ANSI_BOLD}Discovered {len(models)} models:{ANSI_RESET}")
            for m in models:
                print(f"  - {ANSI_CYAN}{m.get('id', 'unknown')}{ANSI_RESET}")
    except Exception as e:
        print(f"  {ANSI_RED}Error: {e}{ANSI_RESET}")


def cmd_install(args: list[str]) -> None:
    """Add the standalone executable directory to the current user's PATH."""
    if not FROZEN:
        trace(f"{ANSI_YELLOW}Use this command from the standalone Windows executable.{ANSI_RESET}")
        return
    if sys.platform != "win32":
        trace(f"{ANSI_RED}Global installation is supported on Windows only.{ANSI_RESET}")
        return

    import winreg

    install_dir = str(PROXY_DIR.resolve())
    key = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
        try:
            current_path, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path, value_type = "", winreg.REG_EXPAND_SZ

        entries = [entry for entry in current_path.split(";") if entry]
        normalized = os.path.normcase(os.path.normpath(install_dir))
        if any(os.path.normcase(os.path.normpath(entry)) == normalized for entry in entries):
            trace(f"{ANSI_GREEN}Already available globally: {install_dir}{ANSI_RESET}")
            return

        entries.append(install_dir)
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(entries))
        trace(f"{ANSI_GREEN}Added to your PATH: {install_dir}{ANSI_RESET}")
        trace("Close and reopen PowerShell or Command Prompt, then run: opencodex")
    except OSError as e:
        trace(f"{ANSI_RED}Could not update your PATH: {e}{ANSI_RESET}")
    finally:
        if key is not None:
            winreg.CloseKey(key)


DEFAULT_CONFIG = {
    "providers": {},
    "mappings": {},
    "default": None,
}


def cmd_config(args: list[str]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    if args and args[0] == "-e":
        os.startfile(str(CONFIG_FILE))
        return
    from opencodex_proxy.tui import run_tui
    run_tui()


def cmd_tui(args: list[str]) -> None:
    from opencodex_proxy.tui import run_tui
    run_tui()


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "tui": cmd_tui,
    "config": cmd_config,
    "models": cmd_models,
    "discover": cmd_discover,
    "install": cmd_install,
}


def main() -> None:
    # Internal entry point used by the standalone Windows executable to start
    # its background proxy without requiring a separate Python installation.
    if len(sys.argv) > 1 and sys.argv[1] == "--proxy-server":
        from opencodex_proxy.app import main as proxy_main
        proxy_main(sys.argv[2:])
        return

    if len(sys.argv) < 2:
        # The standalone executable is intended to be double-clicked as well
        # as run from a terminal. Open the full manager in either case.
        cmd_tui([])
        return

    if sys.argv[1] in {"-h", "--help", "help"}:
        print(f"{ANSI_BOLD}opencodex - OpenCodeX Proxy CLI{ANSI_RESET}")
        print()
        print("Commands:")
        for name in COMMANDS:
            print(f"  {ANSI_CYAN}{name}{ANSI_RESET}")
        print()
        print(f"  {ANSI_DIM}Usage: opencodex <command> [args]{ANSI_RESET}")
        print(f"  {ANSI_DIM}       opencodex tui        Launch interactive TUI{ANSI_RESET}")
        return

    if sys.argv[1] in {"-v", "--version", "version"}:
        from opencodex_proxy import __version__
        print(f"opencodex {__version__}")
        return

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"{ANSI_RED}Unknown command: {cmd}{ANSI_RESET}")
        return

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
