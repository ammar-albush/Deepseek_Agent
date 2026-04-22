"""
DeepSeek Coder Agent – Core Logic

Dateien werden NICHT automatisch geladen.
- context_files: vom Nutzer manuell ausgewählte Dateien
- read_file Tool: Agent fordert gezielt weitere Dateien an
"""

import os
import re
from pathlib import Path
from typing import Generator, Optional

import anthropic

# ── Skip-Listen für Dateibaum ────────────────────────────────────────────────
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "coverage", ".pytest_cache",
    ".mypy_cache", "target", "out", ".gradle", ".idea",
}
SKIP_EXT = {
    ".pyc", ".pyo", ".class", ".o", ".exe", ".dll", ".so",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".bmp", ".webp",
    ".mp4", ".mp3", ".wav", ".zip", ".tar", ".gz", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".lock",
}


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def read_file(path: str) -> tuple[str, str]:
    """Datei lesen. Gibt (inhalt, fehler) zurück."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        return content, ""
    except Exception as e:
        return "", str(e)


def list_project_files(project_path: str) -> list[str]:
    """Alle lesbaren Dateipfade (relativ) eines Projekts auflisten."""
    root = Path(project_path)
    result: list[str] = []

    def _walk(d: Path) -> None:
        try:
            entries = sorted(d.iterdir(),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            if e.is_dir():
                _walk(e)
            elif e.suffix.lower() not in SKIP_EXT:
                try:
                    result.append(str(e.relative_to(root)))
                except ValueError:
                    pass

    _walk(root)
    return result


def extract_section(text: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def extract_bullets(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = re.sub(r"^[\d]+[.)]\s*", "", line.strip())
        line = re.sub(r"^[-*•]\s*", "", line)
        if line:
            out.append(line)
    return out


def extract_files(exec_block: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(
            r'<file\s+path=["\']([^"\']+)["\']>(.*?)</file>',
            exec_block, re.DOTALL)
    }


# ── System-Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Du bist ein autonomer Software-Entwicklungs-Agent.

# Wie Dateien geschrieben werden
Du hast KEIN write_file-Tool und brauchst keines.
Dateien werden geschrieben, indem du ihren vollständigen Inhalt im
<execution>-Block ausgibst – die GUI-Anwendung übernimmt das Schreiben
automatisch, sobald der Nutzer auf "Änderungen anwenden" klickt.

# Verfügbares Tool
- `read_file`: Liest eine Datei, die du noch nicht im Kontext hast.
  Nutze es BEVOR du eine bestehende Datei änderst.

# Pflichtformat der Antwort

<plan>
- Schritt 1: …
- Schritt 2: …
</plan>

<execution>
<file path="relativer/pfad/datei.ext">
vollständiger Dateiinhalt hier – KEINE Auslassungen, KEIN "..."
</file>
<file path="anderer/pfad.ext">
vollständiger Dateiinhalt
</file>
</execution>

<summary>
- Was wurde erstellt / geändert
- Warum
</summary>

# Regeln
- Gib IMMER den vollständigen Dateiinhalt aus – nie Teilausschnitte oder Platzhalter
- Neue Dateien einfach in <execution> ausgeben – kein Tool nötig
- Bestehende Dateien erst mit `read_file` lesen, dann vollständig neu ausgeben
- Wenn keine Dateien geändert werden: <execution></execution>
- Antworte NICHT mit "Ich habe kein Tool zum Schreiben" – du brauchst keines
"""

READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Liest den Inhalt einer Projektdatei. "
        "Nutze dies wenn du eine Datei analysieren oder ändern willst, "
        "die nicht im aktuellen Kontext ist."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relativer Pfad zur Datei vom Projektstamm aus"
            }
        },
        "required": ["path"]
    }
}


# ── Agent ─────────────────────────────────────────────────────────────────────

class DeepSeekAgent:
    BASE_URL = "https://api.deepseek.com/anthropic"
    MODEL    = "deepseek-chat"

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError(
                "Kein API-Schlüssel.\n"
                "Bitte in der GUI eintragen oder .env anlegen."
            )
        self.client = anthropic.Anthropic(api_key=key, base_url=self.BASE_URL)

    def run(
        self,
        prompt: str,
        context_files: dict[str, str],   # {rel_path: content} vom Nutzer gewählt
        project_root: str = "",           # für Tool-basierte Dateizugriffe
        history: Optional[list] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        enable_thinking: bool = True,
    ) -> Generator[tuple, None, None]:
        """
        Events:
          ("thinking",    str)           – CoT-Inhalt (live)
          ("text",        str)           – Antworttext (live)
          ("tool_call",   str)           – Agent fordert Datei an (Pfad)
          ("tool_result", dict)          – {"path", "ok", "size"} nach Lesen
          ("tool_error",  str)           – Fehler beim Lesen
        """
        if history is None:
            history = []

        # Kontext aus manuell gewählten Dateien aufbauen
        ctx_parts = []
        if context_files:
            ctx_parts.append("## Bereitgestellte Dateien\n")
            for rel, content in context_files.items():
                lang = Path(rel).suffix.lstrip(".") or "text"
                ctx_parts.append(f"### {rel}\n```{lang}\n{content}\n```")
            ctx_parts.append("")

        context  = "\n\n".join(ctx_parts)
        user_msg = f"{context}\n\n---\n\n{prompt}" if context else prompt

        # Nachrichten für diesen Turn aufbauen
        messages = list(history) + [{"role": "user", "content": user_msg}]

        actual_max = max(32768, max_tokens) if enable_thinking else max_tokens

        base_kwargs: dict = dict(
            model=self.MODEL,
            max_tokens=actual_max,
            system=SYSTEM_PROMPT,
            tools=[READ_FILE_TOOL],
        )
        if enable_thinking:
            base_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            base_kwargs["temperature"] = temperature

        # ── Multi-Turn-Schleife (Tool-Calls) ──────────────────────────────────
        import json as _json

        MAX_TOOL_CALLS  = 12   # Gesamtlimit pro Anfrage
        MAX_FILE_READS  = 2    # Wie oft dieselbe Datei gelesen werden darf

        full_text        = ""
        history_updated  = False
        total_tool_calls = 0
        read_cache: dict[str, str] = {}   # path → content (bereits gelesen)
        read_count: dict[str, int] = {}   # path → Anzahl Lesevorgänge

        FORCE_WRITE_MSG = (
            "\n\n[SYSTEM] Du hast diese Datei bereits gelesen. "
            "Stoppe sofort alle weiteren read_file-Aufrufe. "
            "Schreibe JETZT den kompletten <execution>-Block mit den geänderten Dateien. "
            "Keine weiteren Tool-Calls – nur noch Textausgabe."
        )
        LIMIT_REACHED_MSG = (
            "\n\n[SYSTEM] Maximale Anzahl an Datei-Lesevorgängen erreicht. "
            "Schreibe JETZT sofort den <plan>, <execution> und <summary> Block. "
            "Nutze die bereits gelesenen Dateiinhalte aus diesem Gespräch. "
            "Keine weiteren read_file-Aufrufe erlaubt."
        )

        while True:
            current_tool: dict | None  = None
            tool_calls:   list[dict]   = []
            turn_text                  = ""

            # Wenn Limit erreicht: tools deaktivieren, Schreib-Zwang
            kwargs = dict(base_kwargs)
            if total_tool_calls >= MAX_TOOL_CALLS:
                kwargs.pop("tools", None)
                # Nachricht ans Modell anfügen, dass es jetzt schreiben muss
                messages = list(messages)
                messages.append({
                    "role": "user",
                    "content": LIMIT_REACHED_MSG,
                })

            with self.client.messages.stream(
                messages=messages, **kwargs
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)

                    if etype == "content_block_start":
                        blk = event.content_block
                        if blk.type == "tool_use":
                            current_tool = {
                                "id":         blk.id,
                                "name":       blk.name,
                                "input_json": "",
                            }

                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", None)
                        if dtype == "thinking_delta":
                            yield ("thinking", delta.thinking)
                        elif dtype == "text_delta":
                            turn_text += delta.text
                            full_text += delta.text
                            yield ("text", delta.text)
                        elif dtype == "input_json_delta" and current_tool:
                            current_tool["input_json"] += delta.partial_json

                    elif etype == "content_block_stop":
                        if current_tool:
                            tool_calls.append(current_tool)
                            current_tool = None

                final_msg = stream.get_final_message()

            # Kein Tool-Call → fertig
            if not tool_calls:
                if not history_updated:
                    history.append({"role": "user",      "content": user_msg})
                history.append({"role": "assistant", "content": full_text})
                break

            # Tool-Calls verarbeiten
            messages.append({"role": "assistant",
                              "content": final_msg.content})
            history_updated = True

            tool_results_msg = []
            for tc in tool_calls:
                total_tool_calls += 1

                try:
                    inp  = _json.loads(tc["input_json"])
                    path = inp.get("path", "")
                except Exception:
                    path = ""

                yield ("tool_call", path)

                read_count[path] = read_count.get(path, 0) + 1
                already_read     = path in read_cache
                duplicate        = read_count[path] > MAX_FILE_READS

                if duplicate:
                    # Schleife erkannt: gecachten Inhalt + Schreib-Befehl
                    yield ("tool_loop", path)
                    result_content = (read_cache.get(path, "[bereits gelesen]")
                                      + FORCE_WRITE_MSG)
                elif already_read:
                    # Zweites Lesen: Inhalt liefern + Hinweis
                    result_content = (read_cache[path]
                                      + "\n\n[SYSTEM] Du hast diese Datei bereits gelesen. "
                                      "Bitte schreibe jetzt den <execution>-Block.")
                    yield ("tool_result", {
                        "path": path, "ok": True, "size": len(read_cache[path]),
                    })
                else:
                    # Erste Mal lesen: normal aus Disk
                    full_path = (os.path.join(project_root, path)
                                 if project_root else path)
                    content, err = read_file(full_path)
                    if err:
                        yield ("tool_error", f"{path}: {err}")
                        result_content = f"[Fehler: {err}]"
                    else:
                        read_cache[path] = content
                        yield ("tool_result", {
                            "path": path, "ok": True, "size": len(content),
                        })
                        result_content = content

                tool_results_msg.append({
                    "type":        "tool_result",
                    "tool_use_id": tc["id"],
                    "content":     result_content,
                })

            messages.append({"role": "user", "content": tool_results_msg})
