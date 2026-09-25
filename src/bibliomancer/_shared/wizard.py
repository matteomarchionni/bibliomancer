"""First-run setup window, shared by both apps.

Shown when there is no config.yaml yet (a fresh install): asks for the
folders the app needs, checks that LM Studio and the model are there (with a
link to download it if not) and offers a desktop icon / Start-menu entry.

    values = run_wizard(spec, fields, lm_check)    # None if cancelled
    fields: [Field(key, label, kind 'dir'|'file', default, hint, required)]
    lm_check(): -> [(ok, message)] lines shown under "LM Studio"
"""

from __future__ import annotations

import os
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from tkinter import filedialog, messagebox

from . import paths
from .theme import T
from .widgets import pick_font

LM_STUDIO_URL = "https://lmstudio.ai"


@dataclass
class Field:
    key: str
    label: str
    kind: str = "dir"            # 'dir' or 'file'
    default: str = ""
    hint: str = ""
    required: bool = True
    local_only: bool = False     # warn if on a cloud drive (SQLite)


def installed_by_installer() -> bool:
    """A Windows installer (Inno Setup) already offered the desktop / Start-menu shortcuts."""
    import sys
    return paths.FROZEN and paths.IS_WIN and os.path.exists(
        os.path.join(os.path.dirname(sys.executable), "unins000.exe"))


def run_wizard(title: str, intro: str, fields: list[Field], lm_check, icon_png: str = "",
               shortcut_names: tuple[str, str] = ("Desktop icon", "Start-menu entry"),
               show_shortcuts: bool | None = None):
    """Modal setup window. Returns {key: value, 'desktop': bool, 'menu': bool} or None."""
    if show_shortcuts is None:
        show_shortcuts = not installed_by_installer()
    root = tk.Tk()
    root.title(f"{title} - setup")
    root.configure(bg=T.BG)
    F = pick_font(root, *T.FONT_TEXT, "TkDefaultFont")
    FD = pick_font(root, *T.FONT_DISPLAY, F)
    f = max(1.0, root.winfo_fpixels("1i") / 96.0)
    S = lambda px: int(round(px * f))  # noqa: E731
    result: dict | None = None
    try:
        if icon_png and os.path.exists(icon_png):
            root._icon = tk.PhotoImage(file=icon_png)
            root.iconphoto(True, root._icon)
    except Exception:
        pass
    body = tk.Frame(root, bg=T.BG, padx=S(28), pady=S(22))
    body.pack(fill="both", expand=True)
    tk.Label(body, text=f"Welcome to {title}", bg=T.BG, fg=T.TEXT,
             font=(FD, 18, "bold")).pack(anchor="w")
    tk.Label(body, text=intro, bg=T.BG, fg=T.MUTED, font=(F, 10), justify="left",
             wraplength=S(620)).pack(anchor="w", pady=(S(4), S(14)))

    vars_: dict[str, tk.StringVar] = {}
    for fd in fields:
        tk.Label(body, text=fd.label + ("" if fd.required else "  (optional)"), bg=T.BG,
                 fg=T.TEXT, font=(F, 10, "bold")).pack(anchor="w", pady=(S(8), S(3)))
        row = tk.Frame(body, bg=T.BG)
        row.pack(fill="x")
        v = tk.StringVar(value=fd.default)
        vars_[fd.key] = v
        tk.Entry(row, textvariable=v, bg=T.SURFACE2, fg=T.TEXT, insertbackground=T.TEXT,
                 relief="flat", highlightthickness=1, highlightbackground=T.BORDER,
                 highlightcolor=T.ACCENT_HI, font=(F, 10)).pack(side="left", fill="x", expand=True,
                                                                ipady=S(4))

        def browse(v=v, fd=fd):
            p = (filedialog.askdirectory(parent=root, title=fd.label, initialdir=v.get() or None)
                 if fd.kind == "dir" else
                 filedialog.askopenfilename(parent=root, title=fd.label))
            if p:
                v.set(os.path.normpath(p))
        tk.Button(row, text="Browse", command=browse, bg=T.SURFACE2, fg=T.TEXT, relief="flat",
                  activebackground=T.BORDER, activeforeground=T.TEXT, padx=S(10),
                  font=(F, 10)).pack(side="left", padx=(S(8), 0))
        if fd.hint:
            tk.Label(body, text=fd.hint, bg=T.BG, fg=T.FAINT, font=(F, 8), justify="left",
                     wraplength=S(620)).pack(anchor="w", pady=(S(2), 0))

    tk.Label(body, text="LM Studio", bg=T.BG, fg=T.TEXT, font=(F, 10, "bold")).pack(
        anchor="w", pady=(S(16), S(3)))
    lm_box = tk.Frame(body, bg=T.BG)
    lm_box.pack(fill="x")

    def show_lm():
        for w in lm_box.winfo_children():
            w.destroy()
        lines = lm_check()
        for good, msg in lines:
            tk.Label(lm_box, text=("✓  " if good else "✗  ") + msg, bg=T.BG,
                     fg=T.GREEN if good else T.AMBER, font=(F, 9), justify="left",
                     wraplength=S(620)).pack(anchor="w")
        r = tk.Frame(lm_box, bg=T.BG)
        r.pack(anchor="w", pady=(S(4), 0))
        if not all(g for g, _m in lines):
            tk.Button(r, text="Download LM Studio", command=lambda: webbrowser.open(LM_STUDIO_URL),
                      bg=T.SURFACE2, fg=T.TEXT, relief="flat", padx=S(10), font=(F, 9)
                      ).pack(side="left")
        tk.Button(r, text="Check again", command=show_lm, bg=T.SURFACE2, fg=T.TEXT, relief="flat",
                  padx=S(10), font=(F, 9)).pack(side="left", padx=(S(8), 0))
    show_lm()

    desk, menu = tk.BooleanVar(value=False), tk.BooleanVar(value=show_shortcuts)
    if show_shortcuts:
        sc = tk.Frame(body, bg=T.BG)
        sc.pack(anchor="w", pady=(S(16), 0))
        for text, var in ((shortcut_names[0], desk), (shortcut_names[1], menu)):
            tk.Checkbutton(sc, text=text, variable=var, bg=T.BG, fg=T.TEXT, selectcolor=T.SURFACE2,
                           activebackground=T.BG, activeforeground=T.TEXT, font=(F, 10)
                           ).pack(side="left", padx=(0, S(18)))

    def finish():
        nonlocal result
        out = {k: v.get().strip() for k, v in vars_.items()}
        for fd in fields:
            val = out[fd.key]
            if fd.required and not val:
                messagebox.showerror("Setup", f"{fd.label}: please choose a folder.", parent=root)
                return
            if val and fd.kind == "dir" and fd.required and not os.path.isdir(val):
                try:
                    os.makedirs(val, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror("Setup", f"{fd.label}: {exc}", parent=root)
                    return
            if val and fd.local_only and paths.looks_like_cloud_folder(val) and not messagebox.askyesno(
                    "Setup", f"{fd.label} looks like a cloud-drive folder. The database there can be "
                             "locked by the sync client mid-run. Use it anyway?", parent=root):
                return
        out.update(desktop=desk.get(), menu=menu.get())
        result = out
        root.destroy()

    btns = tk.Frame(body, bg=T.BG)
    btns.pack(fill="x", pady=(S(20), 0))
    tk.Button(btns, text="Start", command=finish, bg=T.ACCENT, fg=T.ON_ACCENT, relief="flat",
              activebackground=T.ACCENT_HOVER, activeforeground=T.ON_ACCENT, padx=S(22), pady=S(6),
              font=(F, 10, "bold")).pack(side="right")
    tk.Button(btns, text="Cancel", command=root.destroy, bg=T.SURFACE2, fg=T.TEXT, relief="flat",
              padx=S(14), pady=S(6), font=(F, 10)).pack(side="right", padx=(0, S(8)))
    root.update_idletasks()
    w, h = max(S(700), root.winfo_reqwidth()), root.winfo_reqheight()
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+"
                  f"{max(0, (root.winfo_screenheight() - h) // 3)}")
    root.mainloop()
    return result
