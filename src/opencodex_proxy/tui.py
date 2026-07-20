"""
CodexProxy TUI — Interactive terminal UI for managing OpenCodeX Proxy.

Features:
  - Dashboard with live status and quick actions
  - Provider management (add/edit/delete/test/auto-fetch)
  - Model browser with toggle, search, and details
  - Model mapping editor
  - Codex config integration (restart, backup, restore)
  - Real-time log viewer
  - Settings panel
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)

CONFIG_DIR = Path.home() / ".config" / "opencodex-proxy"
CONFIG_FILE = CONFIG_DIR / "config.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CODEX_BACKUP_DIR = Path.home() / ".codex" / "backups"
FROZEN = bool(getattr(sys, "frozen", False))
PROXY_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent.parent.parent
BUNDLED_DIR = Path(getattr(sys, "_MEIPASS", PROXY_DIR))
PID_FILE = PROXY_DIR / "proxy.pid"
LOG_FILE = PROXY_DIR / "proxy.log"
MODEL_CATALOG_DIR = Path.home() / ".codex" / "model-catalogs"
MODEL_CATALOG_FILE = MODEL_CATALOG_DIR / "opencodex.json"
MODEL_CATALOG_JSON_LINE = 'model_catalog_json = "~/.codex/model-catalogs/opencodex.json"'
PROXY_URL = "http://127.0.0.1:8787"

DEFAULT_CONFIG: dict[str, Any] = {
    "providers": {},
    "mappings": {},
    "default": None,
}


def load_config() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


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


def start_proxy() -> tuple[bool, str]:
    if is_proxy_running():
        return True, f"Already running (PID {get_proxy_pid()})"

    config = load_config()
    api_key = os.environ.get("OPENCODE_GO_API_KEY", "")
    base_url = "https://opencode.ai/zen/go/v1"

    providers = config.get("providers", {})
    if providers:
        first = next(iter(providers.values()))
        base_url = first.get("baseUrl", base_url)
        pk = first.get("apiKey", "")
        if pk and not pk.startswith("${"):
            api_key = pk

    if not api_key:
        return False, "No API key — set OPENCODE_GO_API_KEY or add a provider"

    env = os.environ.copy()
    env["OPENCODE_GO_API_KEY"] = api_key

    cmd = ([sys.executable, "--proxy-server"] if FROZEN else [
        sys.executable, "-m", "opencodex_proxy",
    ]) + [
        "--bind", "127.0.0.1", "--port", "8787",
        "--chat-base-url", base_url,
    ]
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROXY_DIR), env=env,
            stdout=open(LOG_FILE, "w"), stderr=subprocess.STDOUT,
        )
        PID_FILE.write_text(str(proc.pid))
        time.sleep(3)
        if is_proxy_running():
            return True, f"Running (PID {proc.pid})"
        return False, "Failed to start — check proxy.log"
    except Exception as e:
        return False, f"Error: {e}"


def stop_proxy() -> tuple[bool, str]:
    pid = get_proxy_pid()
    if not pid:
        return True, "Already stopped"
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        else:
            import signal as sig
            os.kill(pid, sig.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, sig.SIGKILL)
            except OSError:
                pass
    except Exception:
        pass
    PID_FILE.unlink(missing_ok=True)
    return True, "Stopped"


def restart_codex() -> tuple[bool, str]:
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
        return True, "Codex restarted"
    except Exception as e:
        return False, f"Failed: {e}"


def backup_codex_config() -> tuple[bool, str]:
    if not CODEX_CONFIG.exists():
        return False, "No config.toml found"
    CODEX_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CODEX_BACKUP_DIR / f"config-{ts}.toml"
    dest.write_text(CODEX_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return True, f"Backed up to {dest.name}"


def add_provider_to_codex() -> tuple[bool, str]:
    if not CODEX_CONFIG.exists():
        return False, "No config.toml found"
    content = CODEX_CONFIG.read_text(encoding="utf-8")
    if "[model_providers.opencodex]" in content and MODEL_CATALOG_JSON_LINE in content:
        return True, "Already present"
    backup_codex_config()
    provider_block = (
        '[model_providers.opencodex]\n'
        'name = "OpenCodeX"\n'
        'base_url = "http://127.0.0.1:8787/v1"\n'
        'experimental_bearer_token = "any-string-here"\n'
        'wire_api = "responses"\n'
    )
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("[marketplaces."):
            insert_idx = i
            break
    if MODEL_CATALOG_JSON_LINE not in content:
        lines.insert(insert_idx, MODEL_CATALOG_JSON_LINE)
        insert_idx += 1
    if "[model_providers.opencodex]" not in content:
        for pline in provider_block.strip().split("\n"):
            lines.insert(insert_idx, pline)
            insert_idx += 1
    CODEX_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    return True, "Provider added to config.toml"


def remove_provider_from_codex() -> tuple[bool, str]:
    if not CODEX_CONFIG.exists():
        return False, "No config.toml found"
    content = CODEX_CONFIG.read_text(encoding="utf-8")
    if "[model_providers.opencodex]" not in content:
        return True, "Not present"
    lines = content.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.strip() == "[model_providers.opencodex]":
            skip = True
            continue
        if skip and (line.startswith("[") and line.strip() != ""):
            skip = False
        if not skip:
            new_lines.append(line)
    CODEX_CONFIG.write_text("\n".join(new_lines), encoding="utf-8")
    return True, "Provider removed from config.toml"


def _load_catalog_template() -> dict[str, Any] | None:
    """Load the bundled full-format reference catalog as a template."""
    template_path = BUNDLED_DIR / "contrib" / "opencodex-catalog.json"
    if not template_path.exists():
        return None
    try:
        return json.loads(template_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _full_format_model(model_id: str, template: dict[str, Any] | None, context_window: int | None = None) -> dict[str, Any]:
    """Build a full-format model object (same shape as opencodex.json).

    Only slug/display_name/description/context_window vary; everything else is
    copied verbatim from the template so Codex sees a valid full-format entry.
    context_window is used only when the API provides a real value; otherwise a
    safe default (128000) is set. No keyword guessing.

    Also stamps `id`/`context_length`/`modality` so the TUI (which keys models
    by `id`) can render them without a KeyError.
    """
    display_name = model_id.replace("-", " ").replace("_", " ").title()
    ctx = context_window if (isinstance(context_window, int) and context_window > 0) else 128000
    # If the template already has this slug, inherit its full object exactly.
    if template:
        for m in template.get("models", []):
            if isinstance(m, dict) and m.get("slug") == model_id:
                result = dict(m)
                result.setdefault("id", model_id)
                result.setdefault("context_length", ctx)
                return result
        base = dict(template["models"][0]) if template.get("models") else {}
    else:
        base = {}
    base.update({
        "slug": model_id,
        "id": model_id,
        "display_name": display_name,
        "description": f"Model {display_name}.",
        "context_window": ctx,
        "max_context_window": ctx,
        "context_length": ctx,
        "modality": "text",
        "default_reasoning_level": "medium",
    })
    return base


def fetch_models_from_provider(base_url: str, api_key: str) -> list[dict[str, Any]]:
    template = _load_catalog_template()
    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            out = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                if not mid:
                    continue
                # Use API-provided context window if present; else safe default.
                api_ctx = m.get("context_length", m.get("context_window"))
                api_ctx = api_ctx if (isinstance(api_ctx, int) and api_ctx > 0) else None
                full = _full_format_model(mid, template, api_ctx)
                full["enabled"] = True
                out.append(full)
            return out
    except Exception:
        return []


def create_custom_model(model_name: str, context_window: int | None = None) -> dict[str, Any]:
    """Create a user-defined model entry that is retained across fetches."""
    model = _full_format_model(model_name, _load_catalog_template(), context_window)
    model["enabled"] = True
    model["custom"] = True
    return model


def merge_fetched_models(existing: list[dict[str, Any]], fetched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace discovered models while retaining custom IDs absent from the API."""
    fetched_ids = {model_id(model) for model in fetched}
    custom_models = [
        model for model in existing
        if model.get("custom") and model_id(model) not in fetched_ids
    ]
    return [*fetched, *custom_models]


def test_connection(base_url: str, api_key: str) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            count = len(data.get("data", []))
            return True, f"Connected — {count} models found"
    except Exception as e:
        return False, str(e)


def model_id(m: dict[str, Any]) -> str:
    """Return a model's id, tolerating both `id` and `slug` keys."""
    return str(m.get("id") or m.get("slug") or "")


def fetch_proxy_models() -> list[str]:
    try:
        req = urllib.request.Request(f"{PROXY_URL}/v1/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return [m["id"] for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def update_model_catalog() -> tuple[bool, str]:
    """Refresh the model catalog while PRESERVING the exact full-format file.

    Keeps all per-model metadata (base_instructions, model_messages, etc.)
    intact. Only reconciles the slug list against what the proxy serves.
    """
    MODEL_CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    # Seed template from bundled full-format reference catalog.
    template_path = BUNDLED_DIR / "contrib" / "opencodex-catalog.json"
    template_models: dict[str, dict] = {}
    base_template: dict[str, Any] | None = None
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

    # Fetch live slug list from the proxy.
    try:
        req = urllib.request.Request(f"{PROXY_URL}/v1/models")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        live_slugs = [m["id"] for m in data.get("data", []) if m.get("id")]
    except Exception as e:
        return False, f"Proxy not reachable: {e}"

    if not live_slugs:
        return False, "No models from proxy"

    # Load config to get display names for mapped models.
    config = load_config()
    mappings = config.get("mappings", {})
    # Map codex_name -> "Provider:model_name" for display (e.g., gpt-5.5 -> Ollama:minimax-m2.5).
    codex_to_display: dict[str, str] = {}
    for codex_name, target in mappings.items():
        # target is "Provider:model_name" — use it directly as display name.
        codex_to_display[codex_name] = target

    # Build new list preserving full-format metadata.
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
            # Unknown slug (e.g. a user provider model): inherit the full-format
            # structure from the template so Codex displays it correctly, then
            # override the identifying fields.
            if base_template:
                entry = dict(base_template)
            else:
                entry = {}
            # For mapped models, display "Provider:model_name".
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

    catalog.setdefault("fetched_at", "2026-06-22T10:18:00.000000Z")
    catalog.setdefault("etag", 'W/"opencodex-catalog-v0.1.2"')
    catalog.setdefault("client_version", "0.137.0")
    catalog["models"] = new_models

    MODEL_CATALOG_FILE.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return True, f"Catalog updated — {len(new_models)} models (full format preserved)"


def read_proxy_log(lines: int = 100) -> str:
    if not LOG_FILE.exists():
        return "No log file found."
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        all_lines = content.strip().split("\n")
        return "\n".join(all_lines[-lines:])
    except Exception as e:
        return f"Error reading log: {e}"


# ── Modal Screens ────────────────────────────────────────────────


class AddProviderScreen(ModalScreen[dict[str, Any] | None]):
    CSS = """
    AddProviderScreen { align: center middle; }
    #dlg { width: 72; height: auto; max-height: 24; border: thick $primary; background: $surface; padding: 1 2; }
    #dlg Input { width: 100%; margin: 0 0 1 0; }
    #dlg Horizontal { width: 100%; height: auto; margin: 1 0 0 0; }
    #dlg Button { margin: 0 1 0 0; }
    """

    def __init__(self, edit_name: str | None = None, edit_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.edit_name = edit_name
        self.edit_data = edit_data

    def compose(self) -> ComposeResult:
        title = "Edit Provider" if self.edit_name else "Add Provider"
        with Container(id="dlg"):
            yield Static(f"[bold]{title}[/bold]")
            yield Label("Name:")
            yield Input(value=self.edit_name or "", placeholder="e.g. openrouter", id="name")
            yield Label("Base URL:")
            yield Input(
                value=self.edit_data.get("baseUrl", "") if self.edit_data else "",
                placeholder="https://openrouter.ai/api/v1", id="url",
            )
            yield Label("API Key:")
            yield Input(
                value=self.edit_data.get("apiKey", "") if self.edit_data else "",
                placeholder="sk-...", password=True, id="key",
            )
            yield Label("", id="test-result")
            with Horizontal():
                yield Button("Test", id="test-btn", variant="primary")
                yield Button("Save", id="save-btn", variant="success")
                yield Button("Cancel", id="cancel-btn", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "test-btn":
            url = self.query_one("#url", Input).value
            key = self.query_one("#key", Input).value
            if url and key:
                self.query_one("#test-result", Static).update("[yellow]Testing...[/yellow]")
                ok, msg = test_connection(url, key)
                c = "green" if ok else "red"
                self.query_one("#test-result", Static).update(f"[{c}]{msg}[/{c}]")
        elif event.button.id == "save-btn":
            name = self.query_one("#name", Input).value.strip()
            url = self.query_one("#url", Input).value.strip()
            key = self.query_one("#key", Input).value.strip()
            if name and url:
                self.dismiss({"name": name, "baseUrl": url, "apiKey": key})


class AddMappingScreen(ModalScreen[dict[str, str] | None]):
    CSS = """
    AddMappingScreen { align: center middle; }
    #dlg { width: 64; height: auto; max-height: 28; border: thick $primary; background: $surface; padding: 1 2; }
    #dlg Input { width: 100%; margin: 0 0 1 0; }
    #dlg Horizontal { width: 100%; height: auto; margin: 1 0 0 0; }
    #target-search { margin: 0 0 0 0; }
    #target-list { height: 10; border: solid $primary; }
    #target-list ListItem { padding: 0 1; }
    #target-list ListItem:hover { background: $primary-background-darken-2; }
    #target-list ListItem.--selected { background: $primary; color: #ffffff; }
    """

    def __init__(self, available_models: list[str], edit_from: str | None = None, edit_to: str | None = None) -> None:
        super().__init__()
        self.available_models = available_models
        self.edit_from = edit_from
        self.edit_to = edit_to
        self.filtered_models = list(available_models)
        self.selected_target: str = edit_to or ""

    def compose(self) -> ComposeResult:
        title = "Edit Mapping" if self.edit_from else "Add Mapping"
        with Container(id="dlg"):
            yield Static(f"[bold]{title}[/bold]")
            yield Label("Codex Name (what Codex sees):")
            yield Input(value=self.edit_from or "", placeholder="e.g. gpt-4-turbo", id="codex-name")
            yield Label("Target (provider:model-id) — type to search or enter custom:")
            yield Input(value=self.edit_to or "", placeholder="Type to search or enter custom...", id="target-search")
            with ListView(id="target-list"):
                for m in self.filtered_models:
                    yield ListItem(Label(m), name=m)
            with Horizontal():
                yield Button("Save", id="save-btn", variant="success")
                yield Button("Cancel", id="cancel-btn", variant="error")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "target-search":
            q = event.value.lower().strip()
            self.filtered_models = [m for m in self.available_models if q in m.lower()] if q else list(self.available_models)
            lv = self.query_one("#target-list")
            lv.clear()
            for m in self.filtered_models:
                item = ListItem(Label(m), name=m)
                if m == self.selected_target:
                    item.add_class("--selected")
                lv.append(item)
            # Update selected_target with typed value (for custom targets)
            if event.value.strip():
                self.selected_target = event.value.strip()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and event.item.name:
            self.selected_target = str(event.item.name)
            self.query_one("#target-search", Input).value = self.selected_target
            for item in self.query_one("#target-list").query(ListItem):
                item.remove_class("--selected")
                if item.name == self.selected_target:
                    item.add_class("--selected")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "save-btn":
            cn = self.query_one("#codex-name", Input).value.strip()
            tgt = self.query_one("#target-search", Input).value.strip()
            if cn and tgt:
                self.dismiss({"codex_name": cn, "target": tgt})


class AddCustomModelScreen(ModalScreen[dict[str, Any] | None]):
    CSS = """
    AddCustomModelScreen { align: center middle; }
    #dlg { width: 64; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #dlg Input { width: 100%; margin: 0 0 1 0; }
    #dlg Horizontal { width: 100%; height: auto; margin: 1 0 0 0; }
    """

    def __init__(self, provider_name: str) -> None:
        super().__init__()
        self.provider_name = provider_name

    def compose(self) -> ComposeResult:
        with Container(id="dlg"):
            yield Static(f"[bold]Add Custom Model to {self.provider_name}[/bold]")
            yield Label("Model ID (exact upstream model name):")
            yield Input(placeholder="e.g. my-private-model", id="model-name")
            yield Label("Context window (optional; default: 128000):")
            yield Input(placeholder="e.g. 128000", id="context-window")
            with Horizontal():
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "save":
            return

        model_name = self.query_one("#model-name", Input).value.strip()
        raw_context = self.query_one("#context-window", Input).value.strip()
        if not model_name:
            self.notify("Model ID is required", severity="error")
            return
        try:
            context_window = int(raw_context) if raw_context else None
        except ValueError:
            self.notify("Context window must be a number", severity="error")
            return
        if context_window is not None and context_window <= 0:
            self.notify("Context window must be positive", severity="error")
            return
        self.dismiss({"name": model_name, "context_window": context_window})


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; }
    #dlg { width: 50; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #dlg Horizontal { width: 100%; height: auto; margin: 1 0 0 0; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="dlg"):
            yield Static(self.message)
            with Horizontal():
                yield Button("Yes", id="yes", variant="success")
                yield Button("No", id="no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class EditContextScreen(ModalScreen[int | None]):
    CSS = """
    EditContextScreen { align: center middle; }
    #dlg { width: 50; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #dlg Input { width: 100%; margin: 0 0 1 0; }
    #dlg Horizontal { width: 100%; height: auto; margin: 1 0 0 0; }
    """

    def __init__(self, model_name: str, current_ctx: int) -> None:
        super().__init__()
        self.model_name = model_name
        self.current_ctx = current_ctx

    def compose(self) -> ComposeResult:
        with Container(id="dlg"):
            yield Static(f"[bold]Edit Context Window: {self.model_name}[/bold]")
            yield Label("Context window size (tokens):")
            yield Input(value=str(self.current_ctx), placeholder="e.g. 128000", id="ctx-input")
            with Horizontal():
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            val = self.query_one("#ctx-input", Input).value.strip()
            try:
                self.dismiss(int(val))
            except ValueError:
                self.notify("Invalid number", severity="error")


class PickScreen(ModalScreen[str | None]):
    CSS = """
    PickScreen { align: center middle; }
    #dlg { width: 60; height: auto; max-height: 30; border: thick $primary; background: $surface; padding: 1 2; }
    #pick-list { height: auto; max-height: 20; }
    """

    def __init__(self, title: str, items: list[str]) -> None:
        super().__init__()
        self.title_text = title
        self.items = items

    def compose(self) -> ComposeResult:
        with Container(id="dlg"):
            yield Static(f"[bold]{self.title_text}[/bold]")
            with ListView(id="pick-list"):
                for item in self.items:
                    yield ListItem(Label(item), name=item)
            yield Button("Cancel", id="cancel", variant="error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ListItem) and event.item.name:
            self.dismiss(event.item.name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)


# ── Main App ─────────────────────────────────────────────────────


class CodexProxyTUI(App):
    TITLE = "CodexProxy"
    SUB_TITLE = "OpenCodeX Proxy Manager"

    CSS = """
    Screen { background: $background; color: $text; }
    Header { background: $surface; color: $text; }
    Static { color: $text; }
    Label { color: $text; }
    #sidebar { width: 26; dock: left; border-right: solid $primary; background: $surface; padding: 1 0; }
    #sidebar Button { width: 100%; margin: 0 0 1 0; height: 3; }
    #sidebar Button:hover { background: $primary-background-lighten-2; }
    #main { width: 1fr; height: 1fr; }
    #status-bar { dock: bottom; height: 3; background: $surface; border-top: solid $primary; }
    #status-bar Static { width: 1fr; height: 100%; content-align: left middle; padding: 0 2; color: $text; }
    #action-bar { dock: bottom; height: 3; background: $surface; border-top: solid $primary; }
    #action-bar Button { margin: 0 1; }
    #action-bar Button:hover { background: $primary-background-lighten-2; }
    #content-area { width: 1fr; height: 1fr; padding: 1 2; overflow-y: auto; }
    #content { color: $text; }
    #table { height: 1fr; }
    #log-view { height: 1fr; }
    #detail-panel { height: auto; max-height: 8; border: solid $primary; margin: 0 0 1 0; padding: 1; }
    #model-search { display: block; margin: 0 0 1 0; }
    #model-search.hidden { display: none; }
    #list-view { height: 1fr; display: none; }
    #list-view.visible { display: block; }
    #list-view > .provider-header { background: $surface; padding: 0 1; text-style: bold; color: $accent !important; }
    #list-view > .provider-header:hover { background: $primary-background-lighten-2; color: $primary !important; }
    #list-view > .model-item { padding: 0 0 0 2; color: $primary !important; }
    #list-view > .model-item:hover { background: $primary-background-lighten-2; color: $primary !important; }
    #list-view > .model-disabled { color: $text-muted !important; }
    Input { background: $background; color: $text; border: tall $primary; }
    Input:focus { border: tall $primary-lighten-1; }
    ListView { background: $background; }
    ListItem { color: $text; }
    ListItem:hover { background: $primary-background-lighten-2; }
    ListItem.--highlight { background: $primary; color: #ffffff; }
    ListView:focus > ListItem.--highlight { background: $primary; color: #ffffff; }
    DataTable { background: $background; color: $text; }
    DataTable > .datatable--header { background: $surface; color: $text; text-style: bold; }
    DataTable > .datatable--cursor { background: $primary; color: #ffffff; }
    Select { background: $background; color: $text; }
    RichLog { background: $background; color: $text; }
    ModalScreen { background: $background 90%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(Theme(
            name="opencodex",
            background="#f0f4f8",
            surface="#ffffff",
            foreground="#1e293b",
            primary="#2563eb",
            secondary="#3b82f6",
            accent="#7c3aed",
            warning="#f59e0b",
            error="#ef4444",
            success="#10b981",
            dark=False,
            variables={
                "button-color-foreground": "#ffffff",
            },
        ))
        self.theme = "opencodex"
        self.config = load_config()
        self.collapsed: set[str] = set()
        self._refresh_counts()

    def on_mount(self) -> None:
        self._hide_all_views()
        self._show_status_bar()
        self._update_status_bar()
        self._show_view("dashboard")
        self.set_interval(5, self._tick_status)

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "go_back", "Back"),
        Binding("f5", "refresh", "Refresh", show=True),
        Binding("ctrl+s", "start_proxy", "Start Proxy"),
        Binding("ctrl+e", "stop_proxy", "Stop Proxy"),
        Binding("ctrl+f", "toggle_search", "Search", show=True),
        Binding("ctrl+h", "toggle_providers", "Hide/Show", show=True),
    ]

    proxy_status: reactive[str] = reactive("Unknown")
    provider_count: reactive[int] = reactive(0)
    model_count: reactive[int] = reactive(0)
    mapping_count: reactive[int] = reactive(0)
    current_view: reactive[str] = reactive("dashboard")
    model_search: reactive[str] = reactive("")
    show_search: reactive[bool] = reactive(True)
    hidden_providers: reactive[set[str]] = reactive(set())

    def _refresh_counts(self) -> None:
        self.provider_count = len(self.config.get("providers", {}))
        self.model_count = sum(
            len(p.get("models", []))
            for p in self.config.get("providers", {}).values()
        )
        self.mapping_count = len(self.config.get("mappings", {}))

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="sidebar"):
            yield Button("Dashboard", id="nav-dashboard", variant="primary")
            yield Button("Providers", id="nav-providers")
            yield Button("Models", id="nav-models")
            yield Button("Mappings", id="nav-mappings")
            yield Button("Codex", id="nav-codex")
            yield Button("Logs", id="nav-logs")
            yield Button("Settings", id="nav-settings")
        with Container(id="main"):
            with Vertical(id="content-area"):
                yield Input(placeholder="Search models...", id="model-search")
                yield Static("Loading...", id="content")
                yield DataTable(id="table")
                yield ListView(id="list-view")
                yield RichLog(id="log-view")
            with Horizontal(id="action-bar"):
                yield Button("Add", id="btn-add", variant="success")
                yield Button("Edit", id="btn-edit", variant="primary")
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Refresh", id="btn-refresh", variant="default")
                yield Button("Back", id="btn-back", variant="default")
        with Container(id="status-bar"):
            yield Static("", id="status-text")

    def on_mount(self) -> None:
        self._hide_all_views()
        self._show_status_bar()
        self._update_status_bar()
        self._show_view("dashboard")
        self.set_interval(5, self._tick_status)

    def _tick_status(self) -> None:
        running = is_proxy_running()
        self.proxy_status = "Running" if running else "Stopped"
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        running = is_proxy_running()
        pid = get_proxy_pid()
        st = self.query_one("#status-text", Static)
        if running:
            st.update(f"  [green]Proxy: Running[/green] (PID {pid})  |  Providers: {self.provider_count}  |  Models: {self.model_count}  |  Mappings: {self.mapping_count}")
        else:
            st.update(f"  [red]Proxy: Stopped[/red]  |  Providers: {self.provider_count}  |  Models: {self.model_count}  |  Mappings: {self.mapping_count}")

    def _hide_all_views(self) -> None:
        for w in ("content", "table", "list-view", "log-view"):
            self.query_one(f"#{w}").display = False
        for b in ("btn-add", "btn-edit", "btn-delete", "btn-refresh", "btn-back"):
            self.query_one(f"#{b}").display = False

    def _show_status_bar(self) -> None:
        self.query_one("#status-bar").display = True

    def _show_view(self, name: str, **kwargs: Any) -> None:
        self._hide_all_views()
        self.current_view = name
        self._show_status_bar()

        content = self.query_one("#content")

        content.display = True
        content.update("")

        handlers = {
            "dashboard": self._render_dashboard,
            "providers": self._render_providers,
            "models": self._render_models,
            "mappings": self._render_mappings,
            "codex": self._render_codex,
            "logs": self._render_logs,
            "settings": self._render_settings,
        }
        if name in handlers:
            handlers[name](**kwargs)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn.startswith("nav-"):
            view = btn.replace("nav-", "")
            self._show_view(view)
        elif btn == "btn-add":
            self._action_add()
        elif btn == "btn-edit":
            self._action_edit()
        elif btn == "btn-delete":
            self._action_delete()
        elif btn == "btn-refresh":
            self.action_refresh()
        elif btn == "btn-back":
            self._show_view("dashboard")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-search":
            self.model_search = event.value
            self._render_models()

    # ── Actions ──

    def action_refresh(self) -> None:
        self.config = load_config()
        self._refresh_counts()
        self._update_status_bar()
        self._show_view(self.current_view)
        self.notify("Refreshed", severity="success")

    def action_start_proxy(self) -> None:
        self.notify("Starting proxy...", severity="info")
        ok, msg = start_proxy()
        if ok:
            add_provider_to_codex()
            catalog_ok, catalog_msg = update_model_catalog()
            restart_ok, restart_msg = restart_codex()
            if catalog_ok and restart_ok:
                msg = f"{msg}; {catalog_msg}; {restart_msg}"
            elif not catalog_ok:
                msg = f"{msg}; catalog update failed: {catalog_msg}"
            else:
                msg = f"{msg}; {restart_msg}"
        self.notify(msg, severity="success" if ok else "error")
        self.config = load_config()
        self._refresh_counts()
        self._update_status_bar()
        self._show_view(self.current_view)

    def action_stop_proxy(self) -> None:
        self.notify("Stopping proxy...", severity="info")
        ok, msg = stop_proxy()
        self.notify(msg, severity="success" if ok else "error")
        self._update_status_bar()
        self._show_view(self.current_view)

    def action_go_back(self) -> None:
        if self.current_view != "dashboard":
            self._show_view("dashboard")

    def action_toggle_search(self) -> None:
        if self.current_view != "models":
            return
        self.show_search = not self.show_search
        if self.show_search:
            self.query_one("#model-search", Input).focus()
        else:
            self.model_search = ""
            self.query_one("#model-search", Input).value = ""
            self._render_models()

    def action_toggle_providers(self) -> None:
        if self.current_view != "models":
            return
        providers = list(self.config.get("providers", {}).keys())
        if not providers:
            return
        if self.hidden_providers == set(providers):
            self.hidden_providers = set()
            self.notify("All providers shown", severity="success")
        else:
            self.hidden_providers = set(providers)
            self.notify("All providers hidden", severity="success")
        self._render_models()

    # ── Dashboard ──

    def _render_dashboard(self, **_: Any) -> None:
        c = self.query_one("#content")
        running = is_proxy_running()
        pid = get_proxy_pid()
        status_color = "green" if running else "red"
        status_text = "Running" if running else "Stopped"

        lines = [
            "[bold]CodexProxy Dashboard[/bold]",
            "",
            f"  Proxy:    [{status_color}]{status_text}[/{status_color}]" + (f"  (PID {pid})" if pid else ""),
            f"  Providers: {self.provider_count}",
            f"  Models:    {self.model_count}",
            f"  Mappings:  {self.mapping_count}",
            "",
            "[dim]Quick Actions:[/dim]",
            "  [green]Ctrl+S[/green]  Start proxy",
            "  [red]Ctrl+E[/red]   Stop proxy",
            "  [yellow]F5[/yellow]     Refresh",
            "",
        ]

        if self.config.get("providers"):
            lines.append("[bold]Providers:[/bold]")
            for name, p in self.config["providers"].items():
                mc = len(p.get("models", []))
                ec = sum(1 for m in p.get("models", []) if m.get("enabled", True))
                lines.append(f"  [cyan]{name}[/cyan]  {ec}/{mc} models  [dim]{p.get('baseUrl', 'N/A')}[/dim]")
            lines.append("")

        if self.config.get("mappings"):
            lines.append("[bold]Mappings:[/bold]")
            for codex_name, target in self.config["mappings"].items():
                lines.append(f"  [cyan]{codex_name}[/cyan]  ->  [green]{target}[/green]")
            lines.append("")

        c.update("\n".join(lines))
        self.query_one("#btn-add").display = False
        self.query_one("#btn-edit").display = False
        self.query_one("#btn-delete").display = False
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = False

    # ── Providers ──

    def _render_providers(self, **_: Any) -> None:
        c = self.query_one("#content")
        providers = self.config.get("providers", {})
        if not providers:
            c.update("[bold]Providers[/bold]\n\nNo providers configured.\n\nClick [green]Add[/green] to get started.")
        else:
            lines = ["[bold]Providers[/bold]\n"]
            for name, p in providers.items():
                mc = len(p.get("models", []))
                ec = sum(1 for m in p.get("models", []) if m.get("enabled", True))
                lines.append(f"  [cyan]{name}[/cyan]  {ec}/{mc} models")
                lines.append(f"    URL:  {p.get('baseUrl', 'N/A')}")
                lines.append(f"    Key:  {'*' * 8 if p.get('apiKey') else 'N/A'}")
                lines.append("")
            c.update("\n".join(lines))

        self.query_one("#btn-add").display = True
        self.query_one("#btn-edit").display = bool(providers)
        self.query_one("#btn-delete").display = bool(providers)
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    def _action_add(self) -> None:
        if self.current_view == "providers":
            def on_result(result: dict[str, Any] | None) -> None:
                if result:
                    self.config.setdefault("providers", {})[result["name"]] = {
                        "baseUrl": result["baseUrl"],
                        "apiKey": result["apiKey"],
                        "models": [],
                    }
                    save_config(self.config)
                    self._refresh_counts()
                    self._update_status_bar()
                    self.notify(f"Provider '{result['name']}' added", severity="success")
                    self._show_view("providers")
            self.push_screen(AddProviderScreen(), on_result)

        elif self.current_view == "models":
            providers = list(self.config.get("providers", {}).keys())
            if not providers:
                self.notify("No providers — add one first", severity="warning")
                return
            def on_action(action: str | None) -> None:
                if action == "Fetch models from provider":
                    def on_pick(pname: str | None) -> None:
                        if not pname:
                            return
                        pdata = self.config["providers"].get(pname, {})
                        self.notify("Fetching models...", severity="info")
                        models = fetch_models_from_provider(pdata.get("baseUrl", ""), pdata.get("apiKey", ""))
                        if models:
                            pdata["models"] = merge_fetched_models(pdata.get("models", []), models)
                            save_config(self.config)
                            self._refresh_counts()
                            self.notify(f"Fetched {len(models)} models", severity="success")
                            self._show_view("models")
                        else:
                            self.notify("Failed to fetch models", severity="error")
                    self.push_screen(PickScreen("Select Provider to Fetch From", providers), on_pick)
                elif action == "Add custom model":
                    def on_pick(pname: str | None) -> None:
                        if not pname:
                            return
                        def on_result(result: dict[str, Any] | None) -> None:
                            if not result:
                                return
                            pdata = self.config["providers"][pname]
                            models = pdata.setdefault("models", [])
                            name = result["name"]
                            if any(model_id(model) == name for model in models):
                                self.notify(f"Model '{name}' already exists", severity="warning")
                                return
                            models.append(create_custom_model(name, result["context_window"]))
                            save_config(self.config)
                            self._refresh_counts()
                            self.notify(f"Custom model '{name}' added", severity="success")
                            self._show_view("models")
                        self.push_screen(AddCustomModelScreen(pname), on_result)
                    self.push_screen(PickScreen("Select Provider for Custom Model", providers), on_pick)
            self.push_screen(PickScreen("Add Model", ["Fetch models from provider", "Add custom model"]), on_action)

        elif self.current_view == "mappings":
            available = self._get_available_models()
            if not available:
                self.notify("No models available", severity="warning")
                return
            def on_result(result: dict[str, str] | None) -> None:
                if result:
                    self.config.setdefault("mappings", {})[result["codex_name"]] = result["target"]
                    save_config(self.config)
                    self._refresh_counts()
                    self._update_status_bar()
                    self.notify(f"Mapping '{result['codex_name']}' added", severity="success")
                    self._show_view("mappings")
            self.push_screen(AddMappingScreen(available), on_result)

    def _action_edit(self) -> None:
        if self.current_view == "providers":
            providers = list(self.config.get("providers", {}).keys())
            def on_pick(name: str | None) -> None:
                if not name:
                    return
                data = self.config["providers"][name]
                def on_result(result: dict[str, Any] | None) -> None:
                    if result:
                        data["baseUrl"] = result["baseUrl"]
                        data["apiKey"] = result["apiKey"]
                        save_config(self.config)
                        self.notify(f"Provider '{name}' updated", severity="success")
                        self._show_view("providers")
                self.push_screen(AddProviderScreen(edit_name=name, edit_data=data), on_result)
            self.push_screen(PickScreen("Edit Provider", providers), on_pick)

        elif self.current_view == "models":
            providers = list(self.config.get("providers", {}).keys())
            def on_pick_provider(pname: str | None) -> None:
                if not pname:
                    return
                pdata = self.config["providers"][pname]
                models = pdata.get("models", [])
                if not models:
                    self.notify("No models — fetch first (Add)", severity="warning")
                    return
                def on_pick_model(mid: str | None) -> None:
                    if not mid:
                        return
                    for m in models:
                        if model_id(m) == mid:
                            m["enabled"] = not m.get("enabled", True)
                            status = "enabled" if m["enabled"] else "disabled"
                            self.notify(f"Model '{mid}' {status}", severity="success")
                            break
                    save_config(self.config)
                    self._refresh_counts()
                    self._show_view("models")
                self.push_screen(PickScreen("Toggle Model", [model_id(m) for m in models]), on_pick_model)
            self.push_screen(PickScreen("Select Provider", providers), on_pick_provider)

        elif self.current_view == "mappings":
            mappings = list(self.config.get("mappings", {}).keys())
            def on_pick(name: str | None) -> None:
                if not name:
                    return
                available = self._get_available_models()
                if not available:
                    self.notify("No models available", severity="warning")
                    return
                def on_result(result: dict[str, str] | None) -> None:
                    if result:
                        del self.config["mappings"][name]
                        self.config["mappings"][result["codex_name"]] = result["target"]
                        save_config(self.config)
                        self._refresh_counts()
                        self._update_status_bar()
                        self.notify("Mapping updated", severity="success")
                        self._show_view("mappings")
                self.push_screen(AddMappingScreen(available, edit_from=name, edit_to=self.config["mappings"].get(name)), on_result)
            self.push_screen(PickScreen("Edit Mapping", mappings), on_pick)

    def _action_delete(self) -> None:
        if self.current_view == "providers":
            providers = list(self.config.get("providers", {}).keys())
            def on_pick(name: str | None) -> None:
                if not name:
                    return
                def on_confirm(yes: bool) -> None:
                    if yes:
                        del self.config["providers"][name]
                        save_config(self.config)
                        self._refresh_counts()
                        self._update_status_bar()
                        self.notify(f"Provider '{name}' deleted", severity="success")
                        self._show_view("providers")
                self.push_screen(ConfirmScreen(f"Delete provider '{name}'?"), on_confirm)
            self.push_screen(PickScreen("Delete Provider", providers), on_pick)

        elif self.current_view == "mappings":
            mappings = list(self.config.get("mappings", {}).keys())
            def on_pick(name: str | None) -> None:
                if not name:
                    return
                def on_confirm(yes: bool) -> None:
                    if yes:
                        del self.config["mappings"][name]
                        save_config(self.config)
                        self._refresh_counts()
                        self._update_status_bar()
                        self.notify(f"Mapping '{name}' deleted", severity="success")
                        self._show_view("mappings")
                self.push_screen(ConfirmScreen(f"Delete mapping '{name}'?"), on_confirm)
            self.push_screen(PickScreen("Delete Mapping", mappings), on_pick)

    # ── Models ──

    def _render_models(self, **_: Any) -> None:
        c = self.query_one("#content")
        lv = self.query_one("#list-view")
        providers = self.config.get("providers", {})
        
        # Toggle search bar visibility
        search_input = self.query_one("#model-search", Input)
        if self.show_search:
            search_input.remove_class("hidden")
        else:
            search_input.add_class("hidden")

        if not providers:
            c.display = True
            lv.display = False
            c.update("[bold]Models[/bold]\n\nNo providers configured.\n\nAdd a provider first, then use [green]Add[/green] to fetch models.")
            self.query_one("#btn-add").display = True
            self.query_one("#btn-edit").display = False
            self.query_one("#btn-delete").display = False
            self.query_one("#btn-refresh").display = True
            self.query_one("#btn-back").display = True
            return

        # Use the ListView for clickable model list
        c.display = False
        lv.display = True
        lv.clear()

        search = self.model_search.lower().strip()
        found_any = False

        for pname, pdata in providers.items():
            is_hidden = pname in self.hidden_providers
            is_collapsed = pname in self.collapsed or is_hidden
            models = pdata.get("models", [])
            ec = sum(1 for m in models if m.get("enabled", True))
            
            # Filter models by search
            if search:
                filtered = [m for m in models if search in model_id(m).lower() or search in pname.lower()]
                if not filtered:
                    continue
                models = filtered
                found_any = True
            elif models:
                found_any = True

            # Provider header — clickable to toggle collapse
            icon = "[--]" if is_hidden else ("[-]" if is_collapsed else "[+]")
            hidden_tag = " [red](hidden)[/red]" if is_hidden else ""
            header = f" {icon} [bold]{pname}[/bold]{hidden_tag}  ({ec}/{len(models)} enabled)"
            provider_item = ListItem(Label(header), name=f"__provider__{pname}")
            provider_item.add_class("provider-header")
            lv.append(provider_item)

            # Model items under this provider
            if not is_collapsed:
                for m in models:
                    mid = model_id(m)
                    enabled = m.get("enabled", True)
                    status = "[green]ON[/green] " if enabled else "[red]OFF[/red]"
                    ctx = m.get("context_length", "?")
                    mod = m.get("modality", "")
                    label_text = f"  {status}  {mid}  [dim]{ctx} ctx  {mod}[/dim]"
                    model_item = ListItem(Label(label_text), name=f"__model__{pname}__{mid}")
                    model_item.add_class("model-item")
                    if not enabled:
                        model_item.add_class("model-disabled")
                    lv.append(model_item)

        if not found_any:
            lv.append(ListItem(Label("  [dim]No models match search.[/dim]"), name="__empty__"))

        self.query_one("#btn-add").display = True
        self.query_one("#btn-edit").display = True
        self.query_one("#btn-delete").display = False
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = event.item.name if event.item else ""
        if name.startswith("__provider__"):
            pname = name[len("__provider__"):]
            # Toggle collapse
            if pname in self.collapsed:
                self.collapsed.discard(pname)
            else:
                self.collapsed.add(pname)
            self._render_models()
        elif name.startswith("__model__"):
            parts = name.split("__", 3)
            if len(parts) == 4:
                _, _, pname, mid = parts
                pdata = self.config.get("providers", {}).get(pname, {})
                for m in pdata.get("models", []):
                    if model_id(m) == mid:
                        # Show options: toggle enable or edit context
                        options = [
                            "Disable" if m.get("enabled", True) else "Enable",
                            "Edit Context Window",
                        ]
                        def on_pick(choice: str | None) -> None:
                            if not choice:
                                return
                            if choice in ("Enable", "Disable"):
                                m["enabled"] = choice == "Enable"
                                save_config(self.config)
                                self._refresh_counts()
                                status = "enabled" if m["enabled"] else "disabled"
                                self.notify(f"Model '{mid}' {status}", severity="success")
                                self._render_models()
                            elif choice == "Edit Context Window":
                                current_ctx = m.get("context_length", 128000)
                                def on_ctx(new_ctx: int | None) -> None:
                                    if new_ctx is not None and new_ctx > 0:
                                        m["context_length"] = new_ctx
                                        save_config(self.config)
                                        self.notify(f"Context window set to {new_ctx}", severity="success")
                                        self._render_models()
                                self.push_screen(EditContextScreen(mid, current_ctx), on_ctx)
                        self.push_screen(PickScreen(f"Model: {mid}", options), on_pick)
                        break

    # ── Mappings ──

    def _render_mappings(self, **_: Any) -> None:
        c = self.query_one("#content")
        mappings = self.config.get("mappings", {})
        if not mappings:
            c.update("[bold]Model Mappings[/bold]\n\nNo mappings configured.\n\nClick [green]Add[/green] to create a mapping.")
        else:
            lines = ["[bold]Model Mappings[/bold]\n"]
            lines.append("  [dim]Codex Name  ->  Target (provider:model)[/dim]\n")
            for codex_name, target in mappings.items():
                lines.append(f"  [cyan]{codex_name}[/cyan]  ->  [green]{target}[/green]")
            c.update("\n".join(lines))

        self.query_one("#btn-add").display = True
        self.query_one("#btn-edit").display = bool(mappings)
        self.query_one("#btn-delete").display = bool(mappings)
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    # ── Codex ──

    def _render_codex(self, **_: Any) -> None:
        c = self.query_one("#content")
        running = is_proxy_running()
        has_provider = False
        if CODEX_CONFIG.exists():
            content = CODEX_CONFIG.read_text(encoding="utf-8")
            has_provider = "[model_providers.opencodex]" in content

        backups = []
        if CODEX_BACKUP_DIR.exists():
            backups = sorted(CODEX_BACKUP_DIR.glob("config-*.toml"), reverse=True)[:10]

        lines = [
            "[bold]Codex Integration[/bold]",
            "",
            f"  Config:     {'[green]Found[/green]' if CODEX_CONFIG.exists() else '[red]Not found[/red]'}",
            f"  Provider:   {'[green]Added[/green]' if has_provider else '[yellow]Not added[/yellow]'}",
            f"  Proxy:      {'[green]Running[/green]' if running else '[red]Stopped[/red]'}",
            "",
            "[bold]Actions:[/bold]",
            "  [green]Add Provider[/green]   Inject opencodex into config.toml",
            "  [red]Remove Provider[/red]  Remove from config.toml",
            "  [yellow]Backup Config[/yellow]  Save current config.toml",
            "  [cyan]Restart Codex[/cyan]   Restart the Codex app",
            "  [magenta]Update Catalog[/magenta]  Refresh model catalog",
            "",
        ]

        if backups:
            lines.append("[bold]Recent Backups:[/bold]")
            for b in backups[:5]:
                lines.append(f"  [dim]{b.name}[/dim]")
            lines.append("")

        c.update("\n".join(lines))

        self.query_one("#btn-add").display = False
        self.query_one("#btn-edit").display = False
        self.query_one("#btn-delete").display = False
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    def _render_logs(self, **_: Any) -> None:
        c = self.query_one("#content")
        c.update("[bold]Proxy Logs[/bold]  [dim](last 50 lines)[/dim]\n")
        c.display = True

        logv = self.query_one("#log-view", RichLog)
        logv.display = True
        logv.clear()
        log_text = read_proxy_log(50)
        for line in log_text.split("\n"):
            logv.write(line)

        self.query_one("#btn-add").display = False
        self.query_one("#btn-edit").display = False
        self.query_one("#btn-delete").display = False
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    def _render_settings(self, **_: Any) -> None:
        c = self.query_one("#content")
        default = self.config.get("default", "None")
        theme = self.config.get("theme", "default")
        lines = [
            "[bold]Settings[/bold]",
            "",
            f"  Default model: [cyan]{default or 'None'}[/cyan]",
            f"  Theme:         [cyan]{theme}[/cyan]",
            f"  Config file:   [dim]{CONFIG_FILE}[/dim]",
            f"  Codex config:  [dim]{CODEX_CONFIG}[/dim]",
            f"  Model catalog: [dim]{MODEL_CATALOG_FILE}[/dim]",
            f"  Proxy dir:     [dim]{PROXY_DIR}[/dim]",
            "",
        ]
        lines.append("")
        lines.append("[dim]Use Edit to change theme. Restart TUI to apply.[/dim]")
        lines.append("")
        c.update("\n".join(lines))

        self.query_one("#btn-add").display = False
        self.query_one("#btn-edit").display = True
        self.query_one("#btn-delete").display = False
        self.query_one("#btn-refresh").display = True
        self.query_one("#btn-back").display = True

    # ── Helpers ──

    def _get_available_models(self) -> list[str]:
        result = []
        for pname, pdata in self.config.get("providers", {}).items():
            for m in pdata.get("models", []):
                if m.get("enabled", True):
                    result.append(f"{pname}:{model_id(m)}")
        return result


def run_tui() -> None:
    app = CodexProxyTUI()
    app.run()


if __name__ == "__main__":
    run_tui()
