"""
DeepSeek Coder Agent – Modern GUI
- Manuelle Dateiauswahl per Klick/Checkbox
- Agent fordert fehlende Dateien via Tool-Call an
- Python 3.10–3.14 kompatibel (kein tk.Tk-Subclassing)
"""

import os
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Farben ────────────────────────────────────────────────────────────────────
C = {
    "bg":      "#1e1e2e",
    "surface": "#313244",
    "overlay": "#45475a",
    "text":    "#cdd6f4",
    "sub":     "#a6adc8",
    "blue":    "#89b4fa",
    "green":   "#a6e3a1",
    "red":     "#f38ba8",
    "yellow":  "#f9e2af",
    "teal":    "#94e2d5",
    "mauve":   "#cba6f7",
    "pink":    "#f5c2e7",
    "peach":   "#fab387",
    "selected":"#1e4a2e",   # Hintergrund für gewählte Dateien im Baum
}

PHASES = {
    "plan":      ("📋  Erstellt Plan...",   C["blue"]),
    "execution": ("⚙️   Implementiert...",  C["teal"]),
    "summary":   ("✅  Fasst zusammen...",   C["green"]),
}

SKIP_TREE = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".next", ".nuxt"}


# ── Theme ─────────────────────────────────────────────────────────────────────

def _apply_theme(root: tk.Tk) -> None:
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".",
                background=C["bg"], foreground=C["text"],
                fieldbackground=C["surface"],
                troughcolor=C["overlay"],
                selectbackground=C["blue"],
                selectforeground=C["bg"],
                borderwidth=0, relief="flat",
                font=("Segoe UI", 10))
    for w in ("TFrame", "TLabelFrame"):
        s.configure(w, background=C["bg"], borderwidth=0)
    s.configure("Surface.TFrame", background=C["surface"])
    s.configure("Dark.TEntry",
                fieldbackground=C["overlay"], foreground=C["text"],
                insertcolor=C["text"], borderwidth=0, relief="flat")
    s.configure("Tree.Treeview",
                background=C["surface"], foreground=C["text"],
                fieldbackground=C["surface"],
                rowheight=24, borderwidth=0, font=("Segoe UI", 10))
    s.configure("Tree.Treeview.Heading",
                background=C["overlay"], foreground=C["sub"], borderwidth=0)
    s.map("Tree.Treeview",
          background=[("selected", C["blue"])],
          foreground=[("selected", C["bg"])])
    s.configure("Dark.Vertical.TScrollbar",
                background=C["overlay"], troughcolor=C["surface"],
                arrowcolor=C["sub"], borderwidth=0, relief="flat")
    s.configure("Dark.Horizontal.TScale",
                background=C["bg"], troughcolor=C["overlay"],
                sliderrelief="flat", sliderlength=14)
    s.configure("Dark.TCombobox",
                fieldbackground=C["overlay"], background=C["overlay"],
                foreground=C["text"], arrowcolor=C["text"],
                selectbackground=C["overlay"],
                selectforeground=C["text"], borderwidth=0)
    s.map("Dark.TCombobox",
          fieldbackground=[("readonly", C["overlay"])],
          background=[("readonly", C["overlay"])],
          foreground=[("readonly", C["text"])])


# ── Dateibaum mit Auswahl ─────────────────────────────────────────────────────

class FileTree(ttk.Frame):
    """
    Dateibaum mit zwei unabhängigen Selektionsarten:
      ✓ 📄  Dateiinhalt  → grün  (_selected)
      🌳 📄  Nur im Baum → teal  (_tree_sel)
      ✓🌳 📄 Beides       → gelb  (_selected + _tree_sel)

    Ordner-Icons:
      📁          keine Selektion
      ◐ 📁 n/t    teilweise Inhalt gewählt   – gelb
      ✓ 📁        alle Inhalte gewählt       – grün
      🌳 📁        mindestens eine Datei im Baum markiert – teal
    """

    def __init__(self, master, on_selection_changed=None, **kw):
        super().__init__(master, style="Surface.TFrame", **kw)
        self.on_selection_changed = on_selection_changed
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._paths:    dict[str, str] = {}   # item_id → full_path
        self._selected: dict[str, str] = {}   # rel_path → content
        self._tree_sel: set[str]       = set() # rel_path → nur Baum
        self._root_path: str = ""

        self.tv = ttk.Treeview(self, style="Tree.Treeview",
                               show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(self, orient="vertical",
                            command=self.tv.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Tags
        self.tv.tag_configure("file",        foreground=C["text"])
        self.tv.tag_configure("file_sel",    foreground=C["green"],
                              font=("Segoe UI", 10, "bold"))
        self.tv.tag_configure("file_tree",   foreground=C["teal"])
        self.tv.tag_configure("file_both",   foreground=C["yellow"],
                              font=("Segoe UI", 10, "bold"))
        self.tv.tag_configure("dir",         foreground=C["blue"])
        self.tv.tag_configure("dir_partial", foreground=C["yellow"])
        self.tv.tag_configure("dir_full",    foreground=C["green"],
                              font=("Segoe UI", 10, "bold"))
        self.tv.tag_configure("dir_tree",    foreground=C["teal"])

        self.tv.bind("<Button-3>", self._on_rclick)
        self.tv.bind("<space>",    lambda _: self._handle_item(
            self.tv.selection()[0] if self.tv.selection() else None))

        # Kontextmenü
        self._menu = tk.Menu(self.tv, tearoff=0,
                             bg=C["overlay"], fg=C["text"],
                             activebackground=C["blue"],
                             activeforeground=C["bg"],
                             font=("Segoe UI", 10),
                             borderwidth=0)
        self._menu.add_command(
            label="✓  Dateiinhalt auswählen",
            command=lambda: self._handle_item(
                self.tv.selection()[0] if self.tv.selection() else None,
                force=True))
        self._menu.add_command(
            label="✕  Dateiinhalt abwählen",
            command=lambda: self._handle_item(
                self.tv.selection()[0] if self.tv.selection() else None,
                force=False))
        self._menu.add_separator()
        self._menu.add_command(
            label="🌳  Für Baum markieren",
            command=lambda: self._handle_tree_item(
                self.tv.selection()[0] if self.tv.selection() else None,
                force=True))
        self._menu.add_command(
            label="🌳  Aus Baum entfernen",
            command=lambda: self._handle_tree_item(
                self.tv.selection()[0] if self.tv.selection() else None,
                force=False))
        self._menu.add_separator()
        self._menu.add_command(label="✓  Alles auswählen",
                               command=self.select_all)
        self._menu.add_command(label="✕  Alles abwählen",
                               command=self.deselect_all)

    # ── Laden ─────────────────────────────────────────────────────────────────

    def load(self, root_path: str):
        self.tv.delete(*self.tv.get_children())
        self._paths.clear()
        self._selected.clear()
        self._tree_sel.clear()
        self._root_path = root_path
        if not os.path.isdir(root_path):
            return
        rid = self.tv.insert("", "end",
                             text=f"  📁 {os.path.basename(root_path)}",
                             tags=("dir",), open=True)
        self._paths[rid] = root_path
        self._fill(rid, root_path)
        if self.on_selection_changed:
            self.on_selection_changed(self._selected)

    def _fill(self, pid: str, d: str):
        try:
            entries = sorted(os.scandir(d),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_TREE:
                continue
            if e.is_dir():
                nid = self.tv.insert(pid, "end",
                                     text=f"  📁 {e.name}",
                                     tags=("dir",), open=False)
                self._paths[nid] = e.path
                self._fill(nid, e.path)
            else:
                nid = self.tv.insert(pid, "end",
                                     text=f"  📄 {e.name}",
                                     tags=("file",))
                self._paths[nid] = e.path

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_rclick(self, event):
        item = self.tv.identify_row(event.y)
        if item:
            self.tv.selection_set(item)
            self._menu.post(event.x_root, event.y_root)

    # ── Anzeige-Helper ────────────────────────────────────────────────────────

    def _set_item_display(self, item: str):
        """Setzt Icon und Tag eines Datei-Items basierend auf beiden Selektionen."""
        path = self._paths.get(item, "")
        if not os.path.isfile(path):
            return
        rel  = self._rel(path)
        name = Path(path).name
        in_sel  = rel in self._selected
        in_tree = rel in self._tree_sel
        if in_sel and in_tree:
            self.tv.item(item, text=f"✓🌳 📄 {name}", tags=("file_both",))
        elif in_sel:
            self.tv.item(item, text=f"✓  📄 {name}",  tags=("file_sel",))
        elif in_tree:
            self.tv.item(item, text=f"🌳  📄 {name}",  tags=("file_tree",))
        else:
            self.tv.item(item, text=f"   📄 {name}",   tags=("file",))

    # ── Inhalt-Selektion ──────────────────────────────────────────────────────

    def _handle_item(self, item: str | None, force: bool | None = None):
        if not item:
            return
        path = self._paths.get(item)
        if not path:
            return
        if os.path.isdir(path):
            self._toggle_folder(item, force)
        else:
            self._toggle_file(item, force)
        self._notify()

    def _toggle_file(self, item: str, force: bool | None = None,
                     _update_parents: bool = True):
        path = self._paths.get(item, "")
        rel  = self._rel(path)
        currently = rel in self._selected
        add = (not currently) if force is None else force

        if add and rel not in self._selected:
            try:
                content = Path(path).read_text(encoding="utf-8",
                                               errors="replace")
                self._selected[rel] = content
            except Exception:
                pass
        elif not add and rel in self._selected:
            self._selected.pop(rel)

        self._set_item_display(item)
        if _update_parents:
            self._update_parent_state(self.tv.parent(item))

    def _toggle_folder(self, item: str, force: bool | None = None):
        """Alle Dateien im Ordner (Inhalt) togglen – I/O im Hintergrund."""
        file_items = list(self._iter_file_items(item))
        if not file_items:
            return

        if force is None:
            all_sel = all(
                self._rel(self._paths.get(i, "")) in self._selected
                for i in file_items
            )
            force = not all_sel

        add = force
        path = self._paths.get(item, "")
        name = os.path.basename(path)
        self.tv.item(item, text=f"⏳ 📁 {name}", tags=("dir_partial",))

        def _bg():
            to_add:    dict[str, tuple[str, str]] = {}
            to_remove: list[tuple[str, str]]      = []

            for fitem in file_items:
                fpath = self._paths.get(fitem, "")
                rel   = self._rel(fpath)
                if add and rel not in self._selected:
                    try:
                        content = Path(fpath).read_text(
                            encoding="utf-8", errors="replace")
                        to_add[fitem] = (rel, content)
                    except Exception:
                        pass
                elif not add and rel in self._selected:
                    to_remove.append((fitem, rel))

            def _apply():
                for fitem, (rel, content) in to_add.items():
                    self._selected[rel] = content
                    self._set_item_display(fitem)
                for fitem, rel in to_remove:
                    self._selected.pop(rel, None)
                    self._set_item_display(fitem)
                for iid, p in self._paths.items():
                    if os.path.isdir(p):
                        self._refresh_dir_icon(iid)
                self._notify()

            self.tv.after(0, _apply)

        import threading
        threading.Thread(target=_bg, daemon=True).start()

    # ── Baum-Selektion ────────────────────────────────────────────────────────

    def _handle_tree_item(self, item: str | None, force: bool | None = None):
        if not item:
            return
        path = self._paths.get(item)
        if not path:
            return
        if os.path.isdir(path):
            self._toggle_tree_folder(item, force)
        else:
            self._toggle_tree_file(item, force)
            self._update_parent_state(self.tv.parent(item))
        self._notify()

    def _toggle_tree_file(self, item: str, force: bool | None = None):
        path = self._paths.get(item, "")
        rel  = self._rel(path)
        currently = rel in self._tree_sel
        add = (not currently) if force is None else force
        if add:
            self._tree_sel.add(rel)
        else:
            self._tree_sel.discard(rel)
        self._set_item_display(item)

    def _toggle_tree_folder(self, item: str, force: bool | None = None):
        """Alle Dateien im Ordner für Baum markieren/entfernen (kein I/O)."""
        file_items = list(self._iter_file_items(item))
        if not file_items:
            return
        if force is None:
            all_sel = all(
                self._rel(self._paths.get(i, "")) in self._tree_sel
                for i in file_items
            )
            force = not all_sel
        for fitem in file_items:
            rel = self._rel(self._paths.get(fitem, ""))
            if force:
                self._tree_sel.add(rel)
            else:
                self._tree_sel.discard(rel)
            self._set_item_display(fitem)
        for iid, p in self._paths.items():
            if os.path.isdir(p):
                self._refresh_dir_icon(iid)

    # ── Gemeinsame Helfer ─────────────────────────────────────────────────────

    def _iter_file_items(self, item: str):
        for child in self.tv.get_children(item):
            path = self._paths.get(child, "")
            if os.path.isfile(path):
                yield child
            elif os.path.isdir(path):
                yield from self._iter_file_items(child)

    def _update_parent_state(self, item: str):
        if not item:
            return
        self._refresh_dir_icon(item)
        self._update_parent_state(self.tv.parent(item))

    def _refresh_dir_icon(self, item: str):
        if not item:
            return
        path = self._paths.get(item, "")
        name = os.path.basename(path)
        all_files = list(self._iter_file_items(item))
        if not all_files:
            self.tv.item(item, text=f"  📁 {name}", tags=("dir",))
            return
        n_total   = len(all_files)
        n_sel     = sum(1 for i in all_files
                        if self._rel(self._paths.get(i, "")) in self._selected)
        n_tree    = sum(1 for i in all_files
                        if self._rel(self._paths.get(i, "")) in self._tree_sel)
        # Inhalt-Selektion bestimmt primäres Icon
        if n_sel == 0 and n_tree == 0:
            self.tv.item(item, text=f"  📁 {name}",   tags=("dir",))
        elif n_sel == n_total:
            suffix = "  🌳" if n_tree > 0 else ""
            self.tv.item(item, text=f"✓ 📁 {name}{suffix}", tags=("dir_full",))
        elif n_sel > 0:
            suffix = "  🌳" if n_tree > 0 else ""
            self.tv.item(item,
                         text=f"◐ 📁 {name}  ({n_sel}/{n_total}){suffix}",
                         tags=("dir_partial",))
        else:
            # nur Baum-Markierungen, keine Inhalt-Selektion
            self.tv.item(item, text=f"🌳 📁 {name}  ({n_tree}/{n_total})",
                         tags=("dir_tree",))

    def _rel(self, path: str) -> str:
        try:
            return str(Path(path).relative_to(self._root_path))
        except ValueError:
            return os.path.basename(path)

    def _notify(self):
        if self.on_selection_changed:
            self.on_selection_changed(self._selected)

    def select_all(self):
        root_items = self.tv.get_children("")
        if root_items:
            self._toggle_folder(root_items[0], force=True)
        self._notify()

    def deselect_all(self):
        root_items = self.tv.get_children("")
        if root_items:
            self._toggle_folder(root_items[0], force=False)
        self._notify()

    @property
    def selected(self) -> dict[str, str]:
        return dict(self._selected)

    @property
    def tree_selected(self) -> set[str]:
        """Relative Pfade, die explizit für den Baum markiert wurden."""
        return set(self._tree_sel)


# ── Session-Manager ───────────────────────────────────────────────────────────

class SessionManager:
    """Speichert/lädt Gesprächsverläufe als JSON-Dateien."""
    DIR = Path.home() / ".deepseek_agent" / "sessions"

    def __init__(self):
        self.DIR.mkdir(parents=True, exist_ok=True)

    def save(self, sid: str, history: list,
             project_path: str, preview: str) -> str:
        sid = sid or datetime.now().strftime("%Y%m%d_%H%M%S")
        data = {
            "id":           sid,
            "updated_at":   datetime.now().isoformat(),
            "project_path": project_path,
            "preview":      preview[:100],
            "history":      history,
        }
        (self.DIR / f"{sid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return sid

    def list_all(self) -> list[dict]:
        out = []
        for f in sorted(self.DIR.glob("*.json"), reverse=True):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({
                    "id":           d.get("id", f.stem),
                    "preview":      d.get("preview", ""),
                    "project_path": d.get("project_path", ""),
                    "updated_at":   d.get("updated_at", ""),
                })
            except Exception:
                pass
        return out

    def load(self, sid: str) -> dict | None:
        p = self.DIR / f"{sid}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def delete(self, sid: str):
        p = self.DIR / f"{sid}.json"
        if p.exists():
            p.unlink()

    @staticmethod
    def format_label(s: dict) -> str:
        try:
            dt = datetime.fromisoformat(s["updated_at"])
            ts = dt.strftime("%d.%m  %H:%M")
        except Exception:
            ts = s["id"]
        preview = s["preview"].replace("\n", " ")[:52]
        return f"{ts}  {preview}"


# ── Haupt-App ─────────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DeepSeek Coder Agent")
        self.root.geometry("1540x960")
        self.root.minsize(1100, 700)
        self.root.configure(bg=C["bg"])
        _apply_theme(self.root)

        self._history: list[dict] = []
        self._pending: dict[str, str] = {}
        self._busy            = False
        self._agent           = None
        self._stop_event      = threading.Event()
        self._session_mgr     = SessionManager()
        self._session_id: str | None = None
        self._session_preview = ""

        self.v_path         = tk.StringVar()
        self.v_key          = tk.StringVar(value=os.getenv("ANTHROPIC_API_KEY", ""))
        self.v_temp         = tk.DoubleVar(value=0.7)
        self.v_tokens       = tk.StringVar(value="8192")
        self.v_thinking     = tk.BooleanVar(value=True)
        self.v_include_tree = tk.BooleanVar(value=False)
        self.v_auto_apply   = tk.BooleanVar(value=False)

        self._build()
        self._init_agent()

    def mainloop(self):
        self.root.mainloop()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._sidebar()
        self._center()
        self._panel()

    # -- Sidebar --

    def _sidebar(self):
        sb = tk.Frame(self.root, bg=C["surface"], width=290)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.columnconfigure(0, weight=1)
        sb.rowconfigure(2, weight=3)   # Baum
        sb.rowconfigure(4, weight=1)   # Kontextliste

        # Titel
        tk.Label(sb, text="DATEIEN  /  KONTEXT",
                 bg=C["surface"], fg=C["blue"],
                 font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, padx=12, pady=(12, 6), sticky="w")

        # Pfad-Eingabe
        pf = tk.Frame(sb, bg=C["surface"])
        pf.grid(row=1, column=0, padx=10, pady=(0, 4), sticky="ew")
        pf.columnconfigure(0, weight=1)
        self._pe = ttk.Entry(pf, textvariable=self.v_path,
                             style="Dark.TEntry", font=("Segoe UI", 10))
        self._pe.grid(row=0, column=0, columnspan=2, sticky="ew", ipady=4)
        self._pe.bind("<Return>", lambda _: self._load())
        self._mk_btn(pf, "📂", self._browse, w=3).grid(
            row=1, column=0, padx=(0, 2), pady=(3, 0), sticky="ew")
        self._mk_btn(pf, "↺ Laden", self._load).grid(
            row=1, column=1, padx=(2, 0), pady=(3, 0), sticky="ew")

        # Dateibaum
        self._tree = FileTree(sb, on_selection_changed=self._on_sel_changed)
        self._tree.grid(row=2, column=0, padx=8, pady=(4, 0), sticky="nsew")

        # Trennlinie + Kontext-Header
        tk.Frame(sb, height=1, bg=C["overlay"]).grid(
            row=3, column=0, sticky="ew", padx=8, pady=(6, 0))

        ctx_hdr = tk.Frame(sb, bg=C["surface"])
        ctx_hdr.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 0))
        ctx_hdr.columnconfigure(0, weight=1)

        self._lbl_ctx = tk.Label(ctx_hdr,
                                  text="KONTEXT  (0 Dateien gewählt)",
                                  bg=C["surface"], fg=C["sub"],
                                  font=("Segoe UI", 9, "bold"))
        self._lbl_ctx.grid(row=0, column=0, sticky="w", padx=2)
        self._mk_btn(ctx_hdr, "Alle ✕", self._tree.deselect_all,
                     fg=C["red"], hover=C["overlay"], w=6).grid(
            row=0, column=1)

        # Kontextliste (Scrollable)
        ctx_wrap = tk.Frame(sb, bg=C["surface"])
        ctx_wrap.grid(row=4, column=0, padx=8, pady=(2, 8), sticky="nsew")
        ctx_wrap.rowconfigure(0, weight=1)
        ctx_wrap.columnconfigure(0, weight=1)

        self._ctx_text = tk.Text(
            ctx_wrap, bg="#181825", fg=C["teal"],
            font=("JetBrains Mono", 9),
            height=6, state="disabled",
            relief="flat", borderwidth=0,
            highlightthickness=0,
            padx=6, pady=4)
        ctx_vsb = ttk.Scrollbar(ctx_wrap, orient="vertical",
                                command=self._ctx_text.yview,
                                style="Dark.Vertical.TScrollbar")
        self._ctx_text.configure(yscrollcommand=ctx_vsb.set)
        self._ctx_text.grid(row=0, column=0, sticky="nsew")
        ctx_vsb.grid(row=0, column=1, sticky="ns")

    # -- Center --

    def _center(self):
        c = tk.Frame(self.root, bg=C["bg"])
        c.grid(row=0, column=1, sticky="nsew", padx=6)
        c.columnconfigure(0, weight=1)
        c.rowconfigure(1, weight=1)

        # Toolbar
        tb = tk.Frame(c, bg=C["surface"], height=52)
        tb.grid(row=0, column=0, sticky="ew", pady=(10, 5))
        tb.grid_propagate(False)
        tb.columnconfigure(2, weight=1)

        tk.Label(tb, text="⚡  DeepSeek Coder Agent",
                 bg=C["surface"], fg=C["blue"],
                 font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, padx=14, pady=12, sticky="w")

        self._lbl_status = tk.Label(tb, text="● Bereit",
                                    bg=C["surface"], fg=C["green"],
                                    font=("Segoe UI", 10))
        self._lbl_status.grid(row=0, column=2, sticky="e", padx=8)

        self._mk_btn(tb, "Leeren", self._clear,
                     bg=C["overlay"]).grid(
            row=0, column=3, padx=4, pady=10)

        self._btn_stop = tk.Button(
            tb, text="⏹  Stop",
            command=self._stop,
            bg=C["red"], fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat", padx=10, pady=5,
            state="disabled", cursor="hand2",
            activebackground="#c0526e", activeforeground="white")
        self._btn_stop.grid(row=0, column=4, padx=4, pady=10)

        self._btn_apply = tk.Button(
            tb, text="✓  Änderungen anwenden",
            command=self._apply,
            bg="#40a02b", fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat", padx=10, pady=5,
            state="disabled", cursor="hand2",
            activebackground="#2e7620", activeforeground="white")
        self._btn_apply.grid(row=0, column=5, padx=(0, 12), pady=10)

        # Chat
        cf = tk.Frame(c, bg=C["bg"])
        cf.grid(row=1, column=0, sticky="nsew", pady=5)
        cf.columnconfigure(0, weight=1)
        cf.rowconfigure(0, weight=1)

        self._chat = tk.Text(
            cf, bg="#181825", fg=C["text"],
            font=("JetBrains Mono", 11),
            wrap="word", state="disabled",
            relief="flat", highlightthickness=1,
            highlightbackground=C["overlay"],
            insertbackground=C["text"],
            padx=10, pady=8)
        vsb = ttk.Scrollbar(cf, orient="vertical",
                            command=self._chat.yview,
                            style="Dark.Vertical.TScrollbar")
        self._chat.configure(yscrollcommand=vsb.set)
        self._chat.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Chat-Tags
        self._chat.tag_configure("user",
            foreground=C["blue"],  font=("Segoe UI", 12, "bold"))
        self._chat.tag_configure("meta",
            foreground=C["sub"],   font=("Segoe UI", 10, "italic"))
        self._chat.tag_configure("think_hdr",
            foreground=C["yellow"], font=("Segoe UI", 10, "bold"))
        self._chat.tag_configure("think_body",
            foreground="#6c7086",  font=("JetBrains Mono", 10, "italic"))
        self._chat.tag_configure("think_foot",
            foreground=C["overlay"], font=("Segoe UI", 9))
        self._chat.tag_configure("sec_plan",
            foreground=C["blue"],  font=("Segoe UI", 11, "bold"))
        self._chat.tag_configure("sec_exec",
            foreground=C["teal"],  font=("Segoe UI", 11, "bold"))
        self._chat.tag_configure("sec_sum",
            foreground=C["green"], font=("Segoe UI", 11, "bold"))
        self._chat.tag_configure("bullet",
            foreground=C["text"],  font=("Segoe UI", 11))
        self._chat.tag_configure("file_item",
            foreground=C["peach"], font=("JetBrains Mono", 10))
        self._chat.tag_configure("tool_call",
            foreground=C["yellow"], font=("Segoe UI", 10, "bold"))
        self._chat.tag_configure("tool_ok",
            foreground=C["teal"],  font=("Segoe UI", 10))
        self._chat.tag_configure("tool_err",
            foreground=C["red"],   font=("Segoe UI", 10))
        self._chat.tag_configure("pending",
            foreground=C["mauve"], font=("Segoe UI", 11, "bold"))
        self._chat.tag_configure("system",
            foreground=C["pink"],  font=("Segoe UI", 10, "italic"))
        self._chat.tag_configure("error",
            foreground=C["red"],   font=("Segoe UI", 10))
        self._chat.tag_configure("divider",
            foreground=C["overlay"])

        # Prompt
        bar = tk.Frame(c, bg=C["surface"])
        bar.grid(row=2, column=0, sticky="ew", pady=(5, 10))
        bar.columnconfigure(0, weight=1)

        hdr = tk.Frame(bar, bg=C["surface"])
        hdr.grid(row=0, column=0, columnspan=2,
                 padx=12, pady=(8, 0), sticky="ew")
        hdr.columnconfigure(2, weight=1)
        tk.Label(hdr, text="PROMPT  —  Strg+Enter senden",
                 bg=C["surface"], fg=C["sub"],
                 font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w")

        # Dateibaum-Toggle
        self._cb_tree = tk.Checkbutton(
            hdr, text="🌳 Dateibaum",
            variable=self.v_include_tree,
            bg=C["surface"], fg=C["teal"],
            activebackground=C["surface"],
            selectcolor=C["overlay"],
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._on_tree_toggle)
        self._cb_tree.grid(row=0, column=1, sticky="e", padx=(0, 8))

        # Kontext-Badge in Toolbar
        self._lbl_ctx_badge = tk.Label(hdr,
            text="0 Dateien im Kontext",
            bg=C["surface"], fg=C["sub"],
            font=("Segoe UI", 9))
        self._lbl_ctx_badge.grid(row=0, column=2, sticky="e")

        self._prompt = tk.Text(
            bar, height=5, bg=C["overlay"], fg=C["text"],
            font=("Segoe UI", 12), relief="flat", borderwidth=0,
            insertbackground=C["text"], padx=8, pady=6)
        self._prompt.grid(row=1, column=0, padx=12, pady=8, sticky="ew")
        self._prompt.bind("<Control-Return>", lambda _: self._send())

        self._btn_send = tk.Button(
            bar, text="Senden\n⬆",
            command=self._send,
            bg=C["blue"], fg=C["bg"],
            font=("Segoe UI", 12, "bold"),
            relief="flat", width=8, cursor="hand2",
            activebackground="#6a9fd8",
            activeforeground=C["bg"])
        self._btn_send.grid(row=1, column=1, padx=(0, 12), pady=8)

    # -- Settings panel --

    def _panel(self):
        p = tk.Frame(self.root, bg=C["surface"], width=230)
        p.grid(row=0, column=2, sticky="nsew")
        p.grid_propagate(False)

        def section(txt):
            tk.Label(p, text=txt, bg=C["surface"], fg=C["blue"],
                     font=("Segoe UI", 10, "bold")).pack(
                anchor="w", padx=12, pady=(14, 4))

        def lbl(txt):
            tk.Label(p, text=txt, bg=C["surface"], fg=C["sub"],
                     font=("Segoe UI", 9)).pack(
                anchor="w", padx=12, pady=(5, 1))

        def sep():
            tk.Frame(p, height=1, bg=C["overlay"]).pack(
                fill="x", padx=12, pady=8)

        def pbtn(txt, cmd, bg=C["overlay"], fg=C["text"], hover="#585b70"):
            b = tk.Button(p, text=txt, command=cmd,
                          bg=bg, fg=fg, font=("Segoe UI", 10),
                          relief="flat", cursor="hand2",
                          activebackground=hover, activeforeground=fg,
                          padx=8, pady=4)
            b.pack(fill="x", padx=12, pady=3)
            return b

        section("VERBINDUNG")
        lbl("DeepSeek API-Schlüssel")
        ttk.Entry(p, textvariable=self.v_key, show="*",
                  style="Dark.TEntry",
                  font=("Segoe UI", 10)).pack(
            fill="x", padx=12, pady=(0, 4), ipady=4)
        tk.Label(p, text="deepseek-chat  ✓",
                 bg=C["surface"], fg=C["green"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=12)
        pbtn("Verbinden", self._init_agent)

        sep()
        section("GENERATION")
        lbl("Temperatur")
        tr = tk.Frame(p, bg=C["surface"])
        tr.pack(fill="x", padx=12)
        self._lbl_temp = tk.Label(tr, text=f"{self.v_temp.get():.2f}",
                                   bg=C["surface"], fg=C["teal"],
                                   font=("Segoe UI", 10))
        self._lbl_temp.pack(side="right")
        ttk.Scale(p, from_=0, to=2, orient="horizontal",
                  variable=self.v_temp,
                  command=lambda v: self._lbl_temp.configure(
                      text=f"{float(v):.2f}"),
                  style="Dark.Horizontal.TScale").pack(
            fill="x", padx=12, pady=(2, 6))
        lbl("Max. Token")
        ttk.Combobox(p, textvariable=self.v_tokens,
                     values=["1024","2048","4096","8192","16384","32768"],
                     style="Dark.TCombobox", font=("Segoe UI", 10),
                     state="readonly").pack(
            fill="x", padx=12, pady=(0, 4), ipady=3)

        sep()
        section("THINKING  (CoT)")
        tk.Label(p,
                 text="Tiefes Denken vor Plan & Ausführung",
                 bg=C["surface"], fg=C["sub"],
                 font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(0, 4))
        tk.Checkbutton(p, text="Aktiviert",
                       variable=self.v_thinking,
                       bg=C["surface"], fg=C["yellow"],
                       activebackground=C["surface"],
                       selectcolor=C["overlay"],
                       font=("Segoe UI", 10, "bold"),
                       command=self._on_thinking_toggle).pack(
            anchor="w", padx=12)
        self._lbl_think_info = tk.Label(
            p, text="max_tokens → 32K (auto)",
            bg=C["surface"], fg=C["teal"],
            font=("Segoe UI", 8))
        self._lbl_think_info.pack(anchor="w", padx=12, pady=(2, 4))

        sep()
        section("AUSFÜHRUNG")
        tk.Label(p,
                 text="Dateien sofort schreiben,\nkein manuelles Bestätigen",
                 bg=C["surface"], fg=C["sub"],
                 font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(0, 4))
        tk.Checkbutton(p, text="⚡  Auto-Anwenden",
                       variable=self.v_auto_apply,
                       bg=C["surface"], fg=C["peach"],
                       activebackground=C["surface"],
                       selectcolor=C["overlay"],
                       font=("Segoe UI", 10, "bold"),
                       cursor="hand2").pack(anchor="w", padx=12)
        tk.Label(p,
                 text="⚠  Dateien werden ohne Rückfrage\n    überschrieben",
                 bg=C["surface"], fg=C["yellow"],
                 font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(2, 4))

        sep()
        pbtn("Neues Gespräch", self._new_conv,
             bg=C["overlay"], hover=C["mauve"])

        sep()
        section("SESSIONS")
        tk.Label(p, text="Rechtsklick → löschen",
                 bg=C["surface"], fg=C["sub"],
                 font=("Segoe UI", 8)).pack(
            anchor="w", padx=12, pady=(0, 4))

        sess_wrap = tk.Frame(p, bg=C["surface"])
        sess_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        sess_wrap.columnconfigure(0, weight=1)
        sess_wrap.rowconfigure(0, weight=1)

        self._sess_lb = tk.Listbox(
            sess_wrap,
            bg="#181825", fg=C["sub"],
            selectbackground=C["blue"], selectforeground=C["bg"],
            font=("Segoe UI", 8),
            relief="flat", borderwidth=0,
            highlightthickness=0,
            activestyle="none")
        sess_vsb = ttk.Scrollbar(sess_wrap, orient="vertical",
                                 command=self._sess_lb.yview,
                                 style="Dark.Vertical.TScrollbar")
        self._sess_lb.configure(yscrollcommand=sess_vsb.set)
        self._sess_lb.grid(row=0, column=0, sticky="nsew")
        sess_vsb.grid(row=0, column=1, sticky="ns")

        self._sess_lb.bind("<Double-Button-1>", self._on_session_load)
        self._sess_lb.bind("<Button-3>",        self._on_session_rclick)

        self._sess_menu = tk.Menu(
            self._sess_lb, tearoff=0,
            bg=C["overlay"], fg=C["text"],
            activebackground=C["red"],
            activeforeground="white",
            font=("Segoe UI", 10))
        self._sess_menu.add_command(
            label="▶  Laden",
            command=lambda: self._on_session_load(None))
        self._sess_menu.add_command(
            label="✕  Löschen",
            command=self._on_session_delete)

        self._sessions_data: list[dict] = []
        self._refresh_sessions()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mk_btn(self, parent, text, cmd, bg=C["overlay"], fg=C["text"],
                hover="#585b70", w=None):
        kw = dict(bg=bg, fg=fg, font=("Segoe UI", 9),
                  relief="flat", cursor="hand2",
                  activebackground=hover, activeforeground=fg,
                  padx=5, pady=2)
        if w:
            kw["width"] = w
        return tk.Button(parent, text=text, command=cmd, **kw)

    def _w(self, tag: str, text: str):
        def _do():
            self._chat.configure(state="normal")
            self._chat.insert("end", text, tag)
            self._chat.configure(state="disabled")
            self._chat.see("end")
        self.root.after(0, _do)

    def _status(self, text: str, color: str):
        self.root.after(0, lambda: self._lbl_status.configure(
            text=text, fg=color))

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_sel_changed(self, selected: dict[str, str]):
        n = len(selected)
        color = C["teal"] if n > 0 else C["sub"]
        label = f"KONTEXT  ({n} Datei{'en' if n != 1 else ''} gewählt)"
        badge = f"{n} Datei{'en' if n != 1 else ''} im Kontext"
        self.root.after(0, lambda: (
            self._lbl_ctx.configure(text=label, fg=color),
            self._lbl_ctx_badge.configure(text=badge, fg=color),
            self._refresh_ctx_list(selected)
        ))

    def _refresh_ctx_list(self, selected: dict[str, str]):
        self._ctx_text.configure(state="normal")
        self._ctx_text.delete("1.0", "end")
        for rel in selected:
            size = len(selected[rel])
            self._ctx_text.insert("end", f"✓  {rel}  ({size:,} Zeichen)\n")
        self._ctx_text.configure(state="disabled")

    def _on_thinking_toggle(self):
        self._lbl_think_info.configure(
            text="max_tokens → 32K (auto)"
            if self.v_thinking.get() else "Deaktiviert")

    # ── Agent ─────────────────────────────────────────────────────────────────

    def _init_agent(self):
        from agent import DeepSeekAgent
        key = self.v_key.get().strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        try:
            self._agent = DeepSeekAgent(api_key=key or None)
            self._w("system", "✓  Verbunden  (deepseek-chat)\n\n")
        except Exception as e:
            self._agent = None
            self._w("error", f"✗  {e}\n\n")

    # ── Projekt ───────────────────────────────────────────────────────────────

    def _browse(self):
        p = filedialog.askdirectory(title="Projektverzeichnis")
        if p:
            self.v_path.set(p)
            self._load()

    def _load(self):
        p = self.v_path.get().strip()
        if p and os.path.isdir(p):
            self._tree.load(p)
            self._w("system",
                    f"📁  Projekt geladen: {p}\n"
                    f"    → Klicke Dateien an um sie zum Kontext hinzuzufügen\n\n")

    # ── Dateibaum ──────────────────────────────────────────────────────────────

    def _on_tree_toggle(self):
        if self.v_include_tree.get():
            root = self.v_path.get().strip()
            if not root or not os.path.isdir(root):
                self.v_include_tree.set(False)
                self._w("system", "⚠️  Kein Projektpfad geladen.\n\n")

    def _write_tree_json(self) -> tuple[str, str]:
        """
        Baut project_structure.json aus explizit markierten Baum-Pfaden
        (self._tree.tree_selected) oder – falls keine markiert – aus allen
        Projektdateien. Schreibt die Datei in den Projektstamm.
        Gibt (relativer_pfad, inhalt) oder ("", "") bei Fehler zurück.
        """
        import json
        from agent import list_project_files
        root = self.v_path.get().strip()
        if not root or not os.path.isdir(root):
            return "", ""

        explicit = self._tree.tree_selected          # set[rel_path] oder leer
        if explicit:
            # Nur die explizit markierten Pfade verwenden
            files = sorted(explicit)
            source = f"{len(files)} markierte Pfade"
        else:
            # Fallback: alle Projektdateien scannen
            files = list_project_files(root)
            source = "alle Projektdateien"

        # Verschachtelte Baumstruktur aufbauen
        def _insert(node: dict, parts: list[str]) -> None:
            if not parts:
                return
            name = parts[0]
            rest = parts[1:]
            if rest:
                node.setdefault("dirs", {}).setdefault(name, {})
                _insert(node["dirs"][name], rest)
            else:
                node.setdefault("files", []).append(name)

        tree: dict = {}
        for f in files:
            _insert(tree, list(Path(f.replace("\\", "/")).parts))

        def _to_list(node: dict) -> list:
            result = []
            for dname, child in sorted(node.get("dirs", {}).items()):
                result.append({"type": "dir", "name": dname,
                               "children": _to_list(child)})
            for fname in sorted(node.get("files", [])):
                result.append({"type": "file", "name": fname})
            return result

        payload = {
            "project":   os.path.basename(root.rstrip("/\\")),
            "source":    source,
            "tree":      _to_list(tree),
            "all_files": [f.replace("\\", "/") for f in files],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)

        out_path = Path(root) / "project_structure.json"
        try:
            out_path.write_text(content, encoding="utf-8")
        except Exception as e:
            self._w("error", f"  ✗  Baum konnte nicht geschrieben werden: {e}\n\n")
            return "", ""

        return "project_structure.json", content

    # ── Send ───────────────────────────────────────────────────────────────────

    def _send(self):
        prompt = self._prompt.get("1.0", "end").strip()
        if not prompt or self._busy:
            return
        if not self._agent:
            self._init_agent()
            if not self._agent:
                return

        ctx = dict(self._tree.selected)

        # Dateibaum als echte JSON-Datei schreiben und als Kontext hinzufügen
        tree_included = False
        if self.v_include_tree.get():
            rel, content = self._write_tree_json()
            if rel and content:
                ctx[rel] = content
                tree_included = True

        if not ctx:
            if not messagebox.askyesno(
                "Kein Kontext",
                "Keine Dateien ausgewählt.\n\n"
                "Trotzdem senden (ohne Projektdateien)?"
            ):
                return

        # Session-Vorschau beim ersten Prompt setzen
        if not self._session_preview:
            self._session_preview = prompt[:100]

        self._prompt.delete("1.0", "end")
        self._busy = True
        self._stop_event.clear()
        self._btn_send.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._btn_apply.configure(state="disabled")
        self._pending.clear()

        self._w("divider", "─" * 64 + "\n")
        file_ctx = {k: v for k, v in ctx.items() if k != "project_structure.json"}
        if file_ctx:
            files_txt = ", ".join(list(file_ctx.keys())[:4])
            if len(file_ctx) > 4:
                files_txt += f" … (+{len(file_ctx)-4})"
            self._w("meta",
                    f"  📎  Kontext: {len(file_ctx)} Datei(en)  [{files_txt}]\n\n")
        if tree_included:
            n_tree = len(self._tree.tree_selected)
            scope  = f"{n_tree} markierte Pfade" if n_tree else "alle Projektdateien"
            self._w("meta",
                    f"  🌳  project_structure.json  ({scope})  eingeschlossen\n\n")
        self._w("user", f"  Du:  {prompt}\n\n")

        # Alle tkinter-Variablen sicher im Haupt-Thread auslesen
        settings = {
            "thinking":   self.v_thinking.get(),
            "temp":       self.v_temp.get(),
            "max_tokens": int(self.v_tokens.get()),
        }

        threading.Thread(
            target=self._worker,
            args=(prompt, ctx, self.v_path.get().strip(), settings),
            daemon=True,
        ).start()

    def _worker(self, prompt: str, ctx: dict[str, str], project_root: str,
                settings: dict):
        import re as _re
        try:
            text_buffer    = ""
            thinking_chars = 0
            thinking_open  = False
            use_thinking   = settings["thinking"]

            # ── Live-Parser-Zustand ────────────────────────────────────
            plan_hdr_shown   = False
            exec_hdr_shown   = False
            sum_hdr_shown    = False
            plan_lines_shown = 0
            sum_lines_shown  = 0
            files_opened: set  = set()   # Pfade, für die "📝 Schreibt..." gezeigt
            files_closed: dict = {}      # Pfade → Inhalt (vollständig)

            def _clean_bullet(line: str) -> str:
                line = _re.sub(r"^[\d]+[.)]\s*", "", line.strip())
                return _re.sub(r"^[-*•]\s*", "", line)

            def _feed(buf: str):
                nonlocal plan_hdr_shown, exec_hdr_shown, sum_hdr_shown
                nonlocal plan_lines_shown, sum_lines_shown

                # ── Plan ──────────────────────────────────────────────
                ps = buf.find("<plan>")
                if ps >= 0:
                    if not plan_hdr_shown:
                        plan_hdr_shown = True
                        self._w("sec_plan", "  📋  Plan:\n")
                    pe = buf.find("</plan>")
                    pc = buf[ps + 6 : pe if pe >= 0 else len(buf)]
                    lines = pc.split("\n")
                    limit = len(lines) if pe >= 0 else len(lines) - 1
                    for i in range(plan_lines_shown, limit):
                        b = _clean_bullet(lines[i])
                        if b:
                            self._w("bullet", f"      •  {b}\n")
                    plan_lines_shown = limit

                # ── Execution ─────────────────────────────────────────
                # re.search findet den KOMPLETTEN Block zuverlässig, auch
                # wenn der Puffer Text aus mehreren Turns enthält.
                em = _re.search(r"<execution>(.*?)</execution>",
                                buf, _re.DOTALL)
                if em:
                    if not exec_hdr_shown:
                        exec_hdr_shown = True
                        self._w("sec_exec", "\n  ⚙️   Ausführung:\n")
                    exec_content = em.group(1)
                    for m in _re.finditer(
                        r'<file\s+path=["\']([^"\']+)["\']>(.*?)</file>',
                        exec_content, _re.DOTALL
                    ):
                        path    = m.group(1)
                        content = m.group(2).strip()
                        if path not in files_closed:
                            if path not in files_opened:
                                self._w("file_item",
                                        f"      📝  Schreibt: {path}...\n")
                                files_opened.add(path)
                            n = content.count("\n") + 1 if content else 0
                            self._w("tool_ok",
                                    f"      ✓   {path}  ({n} Zeilen)\n")
                            files_closed[path] = content
                elif buf.find("<execution>") >= 0:
                    # Block noch offen – nur Fortschritts-Anzeige
                    if not exec_hdr_shown:
                        exec_hdr_shown = True
                        self._w("sec_exec", "\n  ⚙️   Ausführung:\n")
                    ep = buf.rfind("<execution>")   # letztes Vorkommen
                    es = buf[ep + 11:]
                    for m in _re.finditer(
                        r'<file\s+path=["\']([^"\']+)["\']>', es
                    ):
                        path = m.group(1)
                        if path not in files_opened and path not in files_closed:
                            self._w("file_item",
                                    f"      📝  Schreibt: {path}...\n")
                            files_opened.add(path)

                # ── Summary ───────────────────────────────────────────
                ss = buf.find("<summary>")
                if ss >= 0:
                    if not sum_hdr_shown:
                        sum_hdr_shown = True
                        self._w("sec_sum", "\n  ✅  Ergebnis:\n")
                    se = buf.find("</summary>")
                    sc = buf[ss + 9 : se if se >= 0 else len(buf)]
                    lines = sc.split("\n")
                    limit = len(lines) if se >= 0 else len(lines) - 1
                    for i in range(sum_lines_shown, limit):
                        b = _clean_bullet(lines[i])
                        if b:
                            self._w("bullet", f"      •  {b}\n")
                    sum_lines_shown = limit

            # ── Streaming ─────────────────────────────────────────────
            self._status(
                "🧠  Denkt..." if use_thinking else "⏳  Verarbeitet...",
                C["yellow"])

            for event_type, data in self._agent.run(
                prompt=prompt,
                context_files=ctx,
                project_root=project_root,
                history=self._history,
                temperature=settings["temp"],
                max_tokens=settings["max_tokens"],
                enable_thinking=use_thinking,
                stop_event=self._stop_event,
            ):
                if event_type == "thinking":
                    thinking_chars += len(data)
                    if not thinking_open:
                        thinking_open = True
                        self._w("think_hdr",
                                "  🧠  Thinking:\n"
                                "  ┄" + "┄" * 60 + "\n")
                    self._w("think_body", data)
                    self._status(
                        f"🧠  Denkt...  ({thinking_chars//5} Wörter)",
                        C["yellow"])

                elif event_type == "text":
                    if thinking_open:
                        thinking_open = False
                        self._w("think_foot",
                                f"\n  ┄" + "┄" * 60 +
                                f"\n  {thinking_chars//5} Wörter  "
                                f"({thinking_chars:,} Zeichen)\n\n")
                    text_buffer += data
                    _feed(text_buffer)
                    # Status je nach aktiver Phase
                    for tag, (phase_txt, phase_col) in PHASES.items():
                        if (f"<{tag}>" in text_buffer
                                and f"</{tag}>" not in text_buffer):
                            self._status(phase_txt, phase_col)
                            break

                elif event_type == "tool_call":
                    self._w("tool_call",
                            f"\n  🔧  Agent fordert Datei an:  {data}\n")

                elif event_type == "tool_result":
                    self._w("tool_ok",
                            f"  ✓   Gelesen:  {data['path']}  "
                            f"({data['size']:,} Zeichen)\n\n")

                elif event_type == "tool_error":
                    self._w("tool_err", f"  ✗  {data}\n\n")

                elif event_type == "tool_loop":
                    self._w("tool_err",
                            f"  ⚠️  Schleife erkannt: {data} bereits gelesen"
                            f" → Schreib-Zwang aktiv\n")

                # ── Stop-Check ────────────────────────────────────────
                if self._stop_event.is_set():
                    self._w("system", "\n  ⏹  Abgebrochen.\n\n")
                    break

            # Thinking schließen falls noch offen
            if thinking_open:
                self._w("think_foot",
                        f"\n  ┄" + "┄" * 60 +
                        f"\n  {thinking_chars//5} Wörter\n\n")

            # ── Sicherheitsnetz: Dateien aus vollständigem Buffer extrahieren ──
            # Fängt alles auf, was der Live-Parser evtl. verpasst hat.
            from agent import extract_section, extract_files as _ef
            _exec_raw = extract_section(text_buffer, "execution") or ""
            if _exec_raw:
                for fp, fc in _ef(_exec_raw).items():
                    if fp not in files_closed:
                        files_closed[fp] = fc
                        if not exec_hdr_shown:
                            exec_hdr_shown = True
                            self._w("sec_exec", "\n  ⚙️   Ausführung:\n")
                        n = fc.count("\n") + 1 if fc else 0
                        self._w("file_item",
                                f"      📝  {fp}  ({n} Zeilen)\n")

            # Fallback: keine Struktur erkannt → Rohtext zeigen
            if not plan_hdr_shown and not sum_hdr_shown and not exec_hdr_shown:
                self._w("bullet", text_buffer[:1200].strip() + "\n\n")
            else:
                self._w("bullet", "\n")

            if files_closed:
                if self.v_auto_apply.get() and project_root:
                    # ── Direkt schreiben ──────────────────────────────
                    self._status("💾  Schreibt...", C["peach"])
                    ok_files, err_files = [], []
                    for rel, content in files_closed.items():
                        try:
                            target = Path(project_root) / rel
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(content, encoding="utf-8")
                            ok_files.append(rel)
                        except Exception as e:
                            err_files.append(f"{rel}: {e}")
                    self._w("sec_sum",
                            f"\n  ⚡  Auto-Anwenden: "
                            f"{len(ok_files)} Datei(en) geschrieben:\n")
                    for f in ok_files:
                        self._w("file_item", f"      ✓  {f}\n")
                    if err_files:
                        self._w("error", "\n  ✗  Schreibfehler:\n")
                        for e in err_files:
                            self._w("error", f"      {e}\n")
                    self._w("bullet", "\n")
                    self.root.after(0, self._load)   # Dateibaum aktualisieren
                else:
                    # ── Manuell bestätigen (bisheriges Verhalten) ─────
                    self._pending = files_closed
                    self._w("pending",
                            f"  💾  {len(files_closed)} Datei(en) bereit  →  "
                            f"\"Änderungen anwenden\"\n\n")
                    self.root.after(
                        0, lambda: self._btn_apply.configure(state="normal"))

            self._status("● Bereit", C["green"])

        except Exception as exc:
            msg = str(exc)
            if "400" in msg and "context length" in msg.lower():
                # Estimate rough token usage
                total_chars = sum(len(v) for v in ctx.items())
                self._w("error",
                    f"\n  ✗  Kontext zu groß für das Modell (max. 131 072 Tokens).\n"
                    f"     Tipps:\n"
                    f"     •  Weniger Dateien auswählen (aktuell: {len(ctx)} Einträge)\n"
                    f"     •  'Neues Gespräch' starten (löscht Gesprächsverlauf)\n"
                    f"     •  Max. Tokens in Einstellungen reduzieren\n\n")
            else:
                self._w("error", f"\n  ✗  Fehler: {exc}\n\n")
            self._status("● Fehler", C["red"])
        finally:
            self._busy = False
            # Session speichern (nur wenn History vorhanden)
            if self._history:
                try:
                    self._session_id = self._session_mgr.save(
                        self._session_id or "",
                        self._history,
                        project_root,
                        self._session_preview,
                    )
                    self.root.after(0, self._refresh_sessions)
                except Exception:
                    pass
            self.root.after(0, lambda: (
                self._btn_send.configure(state="normal"),
                self._btn_stop.configure(state="disabled"),
            ))

    def _clear(self):
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._history.clear()
        self._pending.clear()
        self._btn_apply.configure(state="disabled")
        self._w("system", "Chat geleert.\n\n")

    def _stop(self):
        if self._busy:
            self._stop_event.set()
            self._btn_stop.configure(state="disabled")
            self._status("⏹  Wird gestoppt...", C["red"])

    def _new_conv(self):
        self._history.clear()
        self._pending.clear()
        self._session_id      = None
        self._session_preview = ""
        self._btn_apply.configure(state="disabled")
        self._w("divider", "─" * 64 + "\n")
        self._w("system", "  Neues Gespräch.\n\n")

    # ── Sessions ──────────────────────────────────────────────────────────────

    def _refresh_sessions(self):
        self._sessions_data = self._session_mgr.list_all()
        self._sess_lb.delete(0, "end")
        for s in self._sessions_data:
            self._sess_lb.insert("end", SessionManager.format_label(s))

    def _on_session_rclick(self, event):
        idx = self._sess_lb.nearest(event.y)
        if idx >= 0:
            self._sess_lb.selection_clear(0, "end")
            self._sess_lb.selection_set(idx)
            self._sess_menu.post(event.x_root, event.y_root)

    def _on_session_load(self, _event):
        sel = self._sess_lb.curselection()
        if not sel:
            return
        s = self._sessions_data[sel[0]]
        data = self._session_mgr.load(s["id"])
        if not data:
            return
        self._history         = data.get("history", [])
        self._session_id      = s["id"]
        self._session_preview = s["preview"]
        proj = data.get("project_path", "")
        if proj and os.path.isdir(proj):
            self.v_path.set(proj)
            self._tree.load(proj)
        # Verlauf im Chat anzeigen
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._w("divider", "─" * 64 + "\n")
        self._w("system",
                f"  📂  Session geladen: {s['preview'][:60]}\n"
                f"      {len(self._history)} Nachrichten im Kontext\n\n")
        for msg in self._history:
            role    = msg.get("role", "")
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            if role == "user":
                preview = content.strip()[:200].replace("\n", " ")
                self._w("user", f"  Du:  {preview}\n")
            elif role == "assistant":
                preview = content.strip()[:300].replace("\n", " ")
                self._w("meta", f"  Agent:  {preview}…\n")
        self._w("bullet", "\n")

    def _on_session_delete(self):
        sel = self._sess_lb.curselection()
        if not sel:
            return
        s = self._sessions_data[sel[0]]
        if not messagebox.askyesno(
            "Session löschen",
            f"Session löschen?\n\n{s['preview'][:60]}"
        ):
            return
        self._session_mgr.delete(s["id"])
        if self._session_id == s["id"]:
            self._session_id = None
        self._refresh_sessions()

    # ── Änderungen anwenden ───────────────────────────────────────────────────

    def _apply(self):
        if not self._pending:
            return
        root = self.v_path.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror("Kein Projektpfad",
                                 "Bitte zuerst Projektpfad auswählen.")
            return
        preview = "\n".join(f"  {p}" for p in self._pending)
        if not messagebox.askyesno(
            "Änderungen anwenden",
            f"Dateien werden erstellt / überschrieben:\n\n{preview}\n\nFortfahren?"
        ):
            return
        ok, err = [], []
        for rel, content in self._pending.items():
            try:
                target = Path(root) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                ok.append(rel)
            except Exception as e:
                err.append(f"{rel}: {e}")
        self._w("sec_sum", "\n  ✅  Gespeichert:\n")
        for f in ok:
            self._w("file_item", f"      •  {f}\n")
        if err:
            self._w("error", "\n  ✗  Fehler:\n")
            for e in err:
                self._w("error", f"      {e}\n")
        self._w("bullet", "\n")
        self._pending.clear()
        self._btn_apply.configure(state="disabled")
        self._load()


# ── Start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
