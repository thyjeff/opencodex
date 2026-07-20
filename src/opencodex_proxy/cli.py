"""OpenCodeX CLI entry points."""
from __future__ import annotations

import sys
import os

# Ensure the repo root is on sys.path so we can import the root opencodex.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from opencodex_proxy import __version__


def main() -> None:
    """Default entry: delegate to the root opencodex.py CLI."""
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd in ("-v", "--version", "version"):
            print(f"opencodex {__version__}")
            return
        if cmd in ("-h", "--help", "help"):
            _print_usage()
            return
    # Delegate to the full CLI in the root opencodex.py
    import opencodex  # type: ignore[import-not-found]
    opencodex.main()


def start() -> None:
    """Start the proxy server."""
    import opencodex  # type: ignore[import-not-found]
    opencodex.cmd_start([])


def stop() -> None:
    """Stop the proxy server."""
    import opencodex  # type: ignore[import-not-found]
    opencodex.cmd_stop([])


def tui() -> None:
    """Launch the TUI manager."""
    from opencodex_proxy.tui import CodexProxyTUI
    app = CodexProxyTUI()
    app.run()


def _print_usage() -> None:
    print("""opencodex — OpenCodeX Proxy Manager

Usage:
  opencodex              Launch TUI (default)
  opencodex start        Start the proxy + Codex integration
  opencodex stop         Stop the proxy + restore Codex
  opencodex restart      Stop then start
  opencodex status       Show proxy and Codex status
  opencodex tui          Launch the TUI manager
  opencodex config       Open config editor (TUI)
  opencodex models       List available models
  opencodex discover URL Discover models from a URL
  opencodex --version    Show version
  opencodex --help       Show this help
""")
