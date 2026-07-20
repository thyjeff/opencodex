import os
import subprocess
import unittest
from http import HTTPStatus
from unittest import mock

from opencodex_proxy.app import ProxyConfig, ProxyError, resolve_api_key


def make_config() -> ProxyConfig:
    return ProxyConfig(
        bind="127.0.0.1",
        port=8787,
        chat_base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_GO_API_KEY",
        timeout_sec=1,
        max_body_bytes=20 * 1024 * 1024,
    )


class ResolveRouteTests(unittest.TestCase):
    def test_colon_format_routes_to_provider(self) -> None:
        cfg = ProxyConfig(
            bind="127.0.0.1", port=8787,
            chat_base_url="https://opencode.ai/zen/go/v1",
            api_key_env="OPENCODE_GO_API_KEY",
            timeout_sec=1, max_body_bytes=20 * 1024 * 1024,
            providers={"Ollama": {"base_url": "https://ollama.com/v1", "api_key": "k"}},
        )
        pname, mname, base, key = cfg.resolve_route("Ollama:minimax-m2.5")
        self.assertEqual(pname, "Ollama")
        self.assertEqual(mname, "minimax-m2.5")
        self.assertEqual(base, "https://ollama.com/v1")
        self.assertEqual(key, "k")

    def test_colon_format_unknown_provider_falls_through(self) -> None:
        cfg = make_config()
        pname, mname, base, key = cfg.resolve_route("Unknown:model")
        self.assertEqual(pname, "default")

    def test_explicit_mapping_still_works(self) -> None:
        cfg = ProxyConfig(
            bind="127.0.0.1", port=8787,
            chat_base_url="https://opencode.ai/zen/go/v1",
            api_key_env="OPENCODE_GO_API_KEY",
            timeout_sec=1, max_body_bytes=20 * 1024 * 1024,
            providers={"Ollama": {"base_url": "https://ollama.com/v1", "api_key": "k"}},
            mappings={"gpt-5.5": "Ollama:minimax-m2.5"},
        )
        pname, mname, base, key = cfg.resolve_route("gpt-5.5")
        self.assertEqual(pname, "Ollama")
        self.assertEqual(mname, "minimax-m2.5")

    def test_bare_model_in_routes_resolves(self) -> None:
        cfg = ProxyConfig(
            bind="127.0.0.1", port=8787,
            chat_base_url="https://opencode.ai/zen/go/v1",
            api_key_env="OPENCODE_GO_API_KEY",
            timeout_sec=1, max_body_bytes=20 * 1024 * 1024,
            providers={"Ollama": {"base_url": "https://ollama.com/v1", "api_key": "k"}},
            routes={"minimax-m2.5": "Ollama"},
        )
        pname, mname, base, key = cfg.resolve_route("minimax-m2.5")
        self.assertEqual(pname, "Ollama")
        self.assertEqual(mname, "minimax-m2.5")


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the module-level cache between tests.
        import opencodex_proxy.app as app_mod
        app_mod._api_key_cache = None

    def test_env_key_wins_without_keychain_lookup(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "env-key"}, clear=True):
            with mock.patch("opencodex_proxy.app.subprocess.run") as run:
                self.assertEqual(resolve_api_key(make_config(), "req"), "env-key")

        run.assert_not_called()

    def test_keychain_lookup_uses_first_line(self) -> None:
        completed = subprocess.CompletedProcess(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "opencodex-api-key", "-w"],
            0,
            stdout="keychain-key\n",
            stderr="",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("opencodex_proxy.app.subprocess.run", return_value=completed):
                self.assertEqual(resolve_api_key(make_config(), "req"), "keychain-key")

    def test_missing_key_names_env_and_keychain(self) -> None:
        completed = subprocess.CompletedProcess(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "opencodex-api-key", "-w"],
            1,
            stdout="",
            stderr="could not be found",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("opencodex_proxy.app.subprocess.run", return_value=completed):
                with self.assertRaises(ProxyError) as ctx:
                    resolve_api_key(make_config(), "req")

        self.assertEqual(ctx.exception.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("$OPENCODE_GO_API_KEY", ctx.exception.message)
        self.assertIn("keychain", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
