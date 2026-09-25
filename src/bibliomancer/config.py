"""Settings: config.yaml merged over built-in defaults (same scheme as PhotoMancer).

Where config.yaml lives (paths.find_config):
  1. BIBLIOMANCER_CONFIG environment variable
  2. next to the code (portable checkout; how this PC has always run)
  3. the per-user config folder (%APPDATA%\\BiblioMancer, ~/Library/...,
     ~/.config/bibliomancer) - installed apps

data_dir (notes, database, caches, reports) defaults to the code folder in a
portable checkout and to Documents/BiblioMancer otherwise. It must be on
a local disk: a run died on 2026-09-22 when Google Drive briefly locked a file.

The old kb_config.json is converted to config.yaml once, automatically.
"""

from __future__ import annotations

import json
import os

from ._shared import paths
from ._shared.lmstudio import DEFAULT_BASE_URL
from ._shared.settings import load_yaml, update_yaml

APP_NAME = "BiblioMancer"
PKG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
APP_HOME = paths.app_home(PKG_FILE)
CONFIG_PATH, PORTABLE = paths.find_config(APP_NAME, PKG_FILE, "config.yaml", "BIBLIOMANCER_CONFIG")
if (not PORTABLE and not os.environ.get("BIBLIOMANCER_CONFIG") and not paths.FROZEN
        and os.path.exists(os.path.join(APP_HOME, "kb_config.json"))):
    CONFIG_PATH, PORTABLE = os.path.join(APP_HOME, "config.yaml"), True    # not migrated yet
CONFIG_DIR = os.path.dirname(CONFIG_PATH)

DEFAULTS = {
    "papers_dir": "",
    "export_dir": "",
    "data_dir": "",
    "model": "qwen/qwen3.5-9b",
    "embedding_model": "text-embedding-nomic-embed-text-v1.5",
    "reasoning_effort": "none",
    "research_domain": "",
    "max_doc_tokens": 9400,
    "max_pages": 12,
    "lms_path": "",
    "base_url": DEFAULT_BASE_URL,
    "parallel": 1,
}

# the commented config.yaml a new install starts from (same place as the tagger's)
TEMPLATE_PATH = paths.resource(PKG_FILE, "assets", "config.example.yaml")


def _template() -> str:
    try:
        with open(TEMPLATE_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


TEMPLATE = _template()


def _abs(p: str) -> str:
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(CONFIG_DIR, p))


def _migrate_json() -> None:
    """kb_config.json (up to 2026-09) -> config.yaml, once."""
    old = os.path.join(CONFIG_DIR, "kb_config.json")
    if os.path.exists(CONFIG_PATH) or not os.path.exists(old):
        return
    try:
        with open(old, encoding="utf-8") as fh:
            values = {k: v for k, v in json.load(fh).items() if k in DEFAULTS}
    except (OSError, ValueError):
        return
    update_yaml(CONFIG_PATH, values, TEMPLATE)
    os.replace(old, old + ".migrated")


def load() -> dict:
    _migrate_json()
    cfg = load_yaml(CONFIG_PATH, DEFAULTS)
    for k in ("papers_dir", "export_dir", "data_dir", "lms_path"):
        if cfg.get(k):
            cfg[k] = _abs(str(cfg[k]))
    if not cfg["data_dir"]:
        cfg["data_dir"] = APP_HOME if PORTABLE else os.path.join(paths.documents_dir(), APP_NAME)
    return cfg


def update(changes: dict) -> None:
    """Write keys into config.yaml, keeping its comments (creates it from TEMPLATE)."""
    update_yaml(CONFIG_PATH, changes, TEMPLATE)


def exists() -> bool:
    return os.path.exists(CONFIG_PATH) or os.path.exists(os.path.join(CONFIG_DIR, "kb_config.json"))
