"""Two kinds of settings, the same in both apps.

  config.yaml       what the pipeline uses (the scheduled run too): folders,
                    model, limits. Commented YAML; the panel edits single keys in
                    place and keeps the comments (update_yaml).
  gui_settings.json panel-only preferences (last options, repeat mode...), in the
                    per-user state folder.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from typing import Any


# ----------------------------------------------------------- config.yaml --
def merge(base: dict, over: dict) -> dict:
    """Recursive dict merge; `over` wins, unknown keys are kept."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: str, defaults: dict) -> dict:
    """config.yaml merged over `defaults` (missing file = defaults; a broken
    file is reported on stderr and ignored, so it cannot brick the panel)."""
    import yaml
    user: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"WARNING: ignoring unreadable {path}: {exc}", file=sys.stderr)
    return merge(defaults, user)


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "''"
    return "'" + str(v).replace("'", "''") + "'"


def update_yaml(path: str, changes: dict, template: str = "") -> None:
    """Write `key` or `section.key` values into a YAML file, keeping comments.

    The line is edited in place (its trailing comment kept); a missing key is
    added to its section, a missing file is started from `template`.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = template.splitlines()
    for dotted, value in changes.items():
        section, _, key = dotted.rpartition(".")
        start, end = 0, len(lines)
        if section:
            start = next((i + 1 for i, ln in enumerate(lines) if ln.startswith(section + ":")), None)
            if start is None:
                lines += [f"{section}:", f"  {key}: {_fmt(value)}"]
                continue
            end = next((i for i in range(start, len(lines))
                        if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("#")),
                       len(lines))
        pat = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:'(?:[^']|'')*'|\"[^\"]*\"|[^#]*?)(\s+#.*)?$")
        for i in range(start, end):
            m = pat.match(lines[i])
            if m and (len(m.group(1)) > 0) == bool(section):
                lines[i] = f"{m.group(1)}{key}: {_fmt(value)}{m.group(2) or ''}"
                break
        else:
            lines.insert(start if section else len(lines),
                         ("  " if section else "") + f"{key}: {_fmt(value)}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


# ------------------------------------------------------ gui_settings.json --
class PanelSettings(dict):
    """dict persisted as JSON; never raises (a panel must always open)."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.update(json.load(fh))
        except Exception:
            pass

    def save(self, **values) -> None:
        self.update(values)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(dict(self), fh, indent=2)
        except Exception:
            pass
