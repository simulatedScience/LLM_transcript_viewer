import argparse
import json
import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
WEB_DIR = SCRIPT_DIR / "web"
SETTINGS_PATH = SCRIPT_DIR / "viewer_settings.json"


def _detect_default_lmstudio_root() -> Path:
    pointer_path = Path(os.path.expanduser(r"~\.lmstudio-home-pointer"))
    lmstudio_home = Path(os.path.expanduser("~/.lmstudio"))

    if pointer_path.exists():
        try:
            pointer_content = pointer_path.read_text(encoding="utf-8").strip()
            if pointer_content:
                lmstudio_home = Path(os.path.expanduser(pointer_content))
        except Exception:
            pass

    return lmstudio_home / "conversations"


def _normalize_path_text(value: Any) -> str:
    return str(Path(os.path.expanduser(str(value))).resolve())


def _effective_lmstudio_root(configured_root: str) -> Path:
    configured = Path(os.path.expanduser(configured_root))
    if configured.exists() and configured.is_dir():
        return configured

    detected = _detect_default_lmstudio_root()
    if detected.exists() and detected.is_dir():
        return detected

    return configured


def _pick_folder_windows_powershell(initial: str) -> Tuple[Optional[str], Optional[str]]:
    initial_safe = initial.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$dialog.SelectedPath = '{initial_safe}';"
        "$dialog.Description = 'Select folder';"
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        "  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8;"
        "  Write-Output $dialog.SelectedPath"
        "}"
    )

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
    except Exception as exc:
        return None, f"PowerShell picker failed: {exc}"

    selected = (result.stdout or "").strip()
    if selected:
        return selected, None
    if result.returncode != 0:
        return None, (result.stderr or "PowerShell picker failed").strip()
    return None, None


@dataclass
class ViewerSettings:
    transcript_root: str
    lmstudio_root: str


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._path = path
        self._settings = ViewerSettings(
            transcript_root=_normalize_path_text(SCRIPT_DIR),
            lmstudio_root=_normalize_path_text(_detect_default_lmstudio_root()),
        )
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        transcript_root = payload.get("transcript_root")
        lmstudio_root = payload.get("lmstudio_root")

        if isinstance(transcript_root, str) and transcript_root.strip():
            self._settings.transcript_root = _normalize_path_text(transcript_root)
        if isinstance(lmstudio_root, str) and lmstudio_root.strip():
            self._settings.lmstudio_root = _normalize_path_text(lmstudio_root)

    def get(self) -> ViewerSettings:
        with self._lock:
            return ViewerSettings(
                transcript_root=self._settings.transcript_root,
                lmstudio_root=self._settings.lmstudio_root,
            )

    def update(self, transcript_root: Optional[str], lmstudio_root: Optional[str]) -> ViewerSettings:
        with self._lock:
            if isinstance(transcript_root, str) and transcript_root.strip():
                self._settings.transcript_root = _normalize_path_text(transcript_root)
            if isinstance(lmstudio_root, str) and lmstudio_root.strip():
                self._settings.lmstudio_root = _normalize_path_text(lmstudio_root)

            self._path.write_text(
                json.dumps(
                    {
                        "transcript_root": self._settings.transcript_root,
                        "lmstudio_root": self._settings.lmstudio_root,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            return ViewerSettings(
                transcript_root=self._settings.transcript_root,
                lmstudio_root=self._settings.lmstudio_root,
            )

@dataclass
class Message:
    role: str
    text: str
    sent_ts: Optional[float]
    thinking_text: Optional[str]
    blocks: List[Dict[str, str]]


@dataclass
class Conversation:
    id: str
    provider: str
    title: str
    source_file: str
    first_ts: Optional[float]
    last_ts: Optional[float]
    model_name: Optional[str]
    messages: List[Message]


class TranscriptStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversations: Dict[str, Conversation] = {}
        self._last_scan_key: Optional[Tuple[Path, Path]] = None

    def scan(self, root: Path, lm_root: Path, force: bool = False) -> None:
        with self._lock:
            root_key = root.resolve()
            lm_root_key = lm_root.resolve()
            scan_key = (root_key, lm_root_key)

            if not force and self._last_scan_key == scan_key and self._conversations:
                return

            conversations: Dict[str, Conversation] = {}

            for conv_file in sorted(root.rglob("conversations.json")):
                conversations.update(self._parse_provider_file(conv_file))

            if lm_root.exists() and lm_root.is_dir():
                conversations.update(self._parse_lmstudio(lm_root))

            self._conversations = conversations
            self._last_scan_key = scan_key

    def list_metadata(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for conv in self._conversations.values():
                result.append(
                    {
                        "id": conv.id,
                        "provider": conv.provider,
                        "title": conv.title,
                        "source_file": conv.source_file,
                        "first_ts": conv.first_ts,
                        "last_ts": conv.last_ts,
                        "model_name": conv.model_name,
                        "message_count": len(conv.messages),
                    }
                )
            return result

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conv = self._conversations.get(conversation_id)
            if not conv:
                return None
            char_count, word_count = self._compute_text_stats(conv.messages)
            return {
                "id": conv.id,
                "provider": conv.provider,
                "title": conv.title,
                "source_file": conv.source_file,
                "first_ts": conv.first_ts,
                "last_ts": conv.last_ts,
                "model_name": conv.model_name,
                "message_count": len(conv.messages),
                "character_count": char_count,
                "word_count": word_count,
                "messages": [
                    {
                        "role": m.role,
                        "text": m.text,
                        "sent_ts": m.sent_ts,
                        "thinking_text": m.thinking_text,
                        "blocks": m.blocks,
                    }
                    for m in conv.messages
                ],
            }

    @staticmethod
    def _compute_text_stats(messages: List[Message]) -> Tuple[int, int]:
        joined = "\n".join(m.text for m in messages if m.text)
        return len(joined), len(joined.split())

    def _parse_provider_file(self, path: Path) -> Dict[str, Conversation]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(payload, list) or not payload:
            return {}

        first = payload[0]
        if not isinstance(first, dict):
            return {}

        parsed: Dict[str, Conversation] = {}

        if "mapping" in first:
            for idx, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                conv = self._from_openai(item, path, idx)
                if conv and conv.messages:
                    parsed[conv.id] = conv

        elif "chat_messages" in first:
            for idx, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                conv = self._from_anthropic(item, path, idx)
                if conv and conv.messages:
                    parsed[conv.id] = conv

        return parsed

    def _parse_lmstudio(self, lm_root: Path) -> Dict[str, Conversation]:
        parsed: Dict[str, Conversation] = {}

        for conv_file in sorted(lm_root.rglob("*.conversation.json")):
            try:
                raw_conv = json.loads(conv_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            conv = self._from_lmstudio(raw_conv, conv_file)
            if conv and conv.messages:
                parsed[conv.id] = conv

        return parsed

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[float]:
        text = TranscriptStore._safe_text(value)
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None

    def _parse_any_datetime(self, value: Any) -> Optional[float]:
        numeric = self._as_float(value)
        if numeric is not None:
            if numeric > 10_000_000_000:
                return numeric / 1000.0
            return numeric
        return self._parse_iso_datetime(value)

    @staticmethod
    def _make_block(block_type: str, text: str) -> Optional[Dict[str, str]]:
        clean = text.strip()
        if not clean:
            return None
        return {"type": block_type, "text": clean}

    @staticmethod
    def _summarize_blocks(blocks: List[Dict[str, str]]) -> Tuple[str, Optional[str]]:
        text = "\n\n".join(block["text"] for block in blocks if block.get("type") == "text").strip()
        thinking_text = "\n\n".join(block["text"] for block in blocks if block.get("type") == "thinking").strip() or None
        return text, thinking_text

    @staticmethod
    def _split_thinking_tag_blocks(raw_text: str) -> List[Dict[str, str]]:
        blocks: List[Dict[str, str]] = []
        text = raw_text or ""
        pattern = re.compile(r"<(thinking|think)>([\s\S]*?)</\\1>", re.IGNORECASE)
        cursor = 0
        for match in pattern.finditer(text):
            before = text[cursor : match.start()]
            if before.strip():
                blocks.append({"type": "text", "text": before.strip()})

            thinking_body = (match.group(2) or "").strip()
            if thinking_body:
                blocks.append({"type": "thinking", "text": thinking_body})

            cursor = match.end()

        after = text[cursor:]
        if after.strip():
            blocks.append({"type": "text", "text": after.strip()})

        if not blocks and text.strip():
            blocks.append({"type": "text", "text": text.strip()})

        return blocks

    def _extract_openai_message_parts(self, content: Dict[str, Any]) -> List[Dict[str, str]]:
        blocks: List[Dict[str, str]] = []

        parts = content.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):
                    maybe = self._make_block("text", self._safe_text(part))
                    if maybe:
                        blocks.append(maybe)
                elif isinstance(part, dict):
                    p_type = self._safe_text(part.get("type")).lower()
                    chunk = self._safe_text(part.get("text"))
                    if p_type in {"thinking", "thought", "reasoning"}:
                        maybe = self._make_block("thinking", chunk)
                    else:
                        maybe = self._make_block("text", chunk)
                    if maybe:
                        blocks.append(maybe)

        if not blocks and isinstance(content.get("text"), str):
            fallback = self._safe_text(content.get("text"))
            maybe = self._make_block("text", fallback)
            if maybe:
                blocks.append(maybe)

        return blocks

    def _extract_anthropic_blocks(self, raw_item: Dict[str, Any]) -> List[Dict[str, str]]:
        raw_text = self._safe_text(raw_item.get("text"))
        blocks: List[Dict[str, str]] = []

        content = raw_item.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                b_type = self._safe_text(block.get("type")).lower()

                if b_type == "text":
                    block_text = self._safe_text(block.get("text"))
                    maybe = self._make_block("text", block_text)
                    if maybe:
                        blocks.append(maybe)
                    continue

                if b_type == "thinking":
                    block_text = self._safe_text(block.get("thinking")) or self._safe_text(block.get("text"))
                    maybe = self._make_block("thinking", block_text)
                    if maybe:
                        blocks.append(maybe)
                    continue

                if b_type == "token_budget":
                    budget = self._safe_text(block.get("budget_tokens")) or self._safe_text(block.get("tokens"))
                    if budget:
                        blocks.append({"type": "thinking", "text": f"Token budget: {budget}"})
                    continue

                if b_type == "tool_use":
                    name = self._safe_text(block.get("name")) or "tool"
                    input_payload = block.get("input")
                    payload_text = self._safe_text(json.dumps(input_payload, ensure_ascii=False, indent=2))
                    line = f"[Tool call: {name}]"
                    if payload_text:
                        line += f"\n{payload_text}"
                    maybe = self._make_block("text", line)
                    if maybe:
                        blocks.append(maybe)
                    continue

                if b_type == "tool_result":
                    result_content = self._safe_text(block.get("content"))
                    if result_content:
                        blocks.append({"type": "text", "text": f"[Tool result]\n{result_content}"})

        if not blocks:
            fallback_thinking = self._safe_text(raw_item.get("thinking"))
            if fallback_thinking:
                blocks.append({"type": "thinking", "text": fallback_thinking})
            if raw_text:
                maybe = self._make_block("text", raw_text)
                if maybe:
                    blocks.append(maybe)
        elif raw_text and not any(block.get("type") == "text" for block in blocks):
            maybe = self._make_block("text", raw_text)
            if maybe:
                blocks.append(maybe)

        # If thinking appears only after all text blocks, treat it as non-interleaved
        # and place it at message start; otherwise keep the original interleaved order.
        text_indices = [i for i, block in enumerate(blocks) if block.get("type") == "text"]
        thinking_indices = [i for i, block in enumerate(blocks) if block.get("type") == "thinking"]
        if text_indices and thinking_indices and min(thinking_indices) > max(text_indices):
            thinking_blocks = [block for block in blocks if block.get("type") == "thinking"]
            text_blocks = [block for block in blocks if block.get("type") != "thinking"]
            blocks = thinking_blocks + text_blocks

        return blocks

    def _extract_lmstudio_content_blocks(self, content: Any, block_type_hint: Optional[str] = None) -> List[Dict[str, str]]:
        blocks: List[Dict[str, str]] = []

        if isinstance(content, str):
            tag_split = self._split_thinking_tag_blocks(content)
            if block_type_hint == "thinking":
                for block in tag_split:
                    text = self._safe_text(block.get("text"))
                    maybe = self._make_block("thinking", text)
                    if maybe:
                        blocks.append(maybe)
            else:
                blocks.extend(tag_split)
            return blocks

        if isinstance(content, list):
            for item in content:
                blocks.extend(self._extract_lmstudio_content_blocks(item, block_type_hint=block_type_hint))
            return blocks

        if not isinstance(content, dict):
            return blocks

        c_type = self._safe_text(content.get("type")).lower()

        if c_type == "text":
            text_value = self._safe_text(content.get("text"))
            if block_type_hint == "thinking":
                maybe = self._make_block("thinking", text_value)
                if maybe:
                    blocks.append(maybe)
            else:
                blocks.extend(self._split_thinking_tag_blocks(text_value))
            return blocks

        if c_type in {"thinking", "reasoning"}:
            text_value = self._safe_text(content.get("thinking")) or self._safe_text(content.get("text"))
            maybe = self._make_block("thinking", text_value)
            if maybe:
                blocks.append(maybe)
            return blocks

        if c_type in {"tool_call", "tool_use"}:
            name = self._safe_text(content.get("name")) or "tool"
            payload = content.get("input")
            payload_text = self._safe_text(json.dumps(payload, ensure_ascii=False, indent=2))
            line = f"[Tool call: {name}]"
            if payload_text:
                line += f"\n{payload_text}"
            maybe = self._make_block("text", line)
            if maybe:
                blocks.append(maybe)
            return blocks

        if c_type in {"tool_result", "tool_output"}:
            result_text = self._safe_text(content.get("content")) or self._safe_text(content.get("text"))
            if result_text:
                blocks.append({"type": "text", "text": f"[Tool result]\n{result_text}"})
            return blocks

        nested_content = content.get("content")
        if nested_content is not None:
            blocks.extend(self._extract_lmstudio_content_blocks(nested_content, block_type_hint=block_type_hint))

        return blocks

    def _extract_lmstudio_message_blocks(self, message: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
        versions = message.get("versions")
        if not isinstance(versions, list) or not versions:
            return "assistant", []

        selected_idx = 0
        raw_selected = message.get("currentlySelected")
        if isinstance(raw_selected, int) and 0 <= raw_selected < len(versions):
            selected_idx = raw_selected

        selected = versions[selected_idx]
        if not isinstance(selected, dict):
            return "assistant", []

        role = self._safe_text(selected.get("role")).lower() or "assistant"
        blocks: List[Dict[str, str]] = []

        content = selected.get("content")
        if content is not None:
            blocks.extend(self._extract_lmstudio_content_blocks(content))

        steps = selected.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                if self._safe_text(step.get("type")).lower() != "contentblock":
                    continue
                style = step.get("style")
                style_type = ""
                if isinstance(style, dict):
                    style_type = self._safe_text(style.get("type")).lower()
                hint = "thinking" if style_type == "thinking" else None
                blocks.extend(self._extract_lmstudio_content_blocks(step.get("content"), block_type_hint=hint))

        return role, blocks

    def _from_openai(self, raw: Dict[str, Any], source_file: Path, idx: int) -> Optional[Conversation]:
        mapping = raw.get("mapping") or {}
        extracted: List[Tuple[float, Message]] = []
        timestamps: List[float] = []

        if not isinstance(mapping, dict):
            return None

        for node in mapping.values():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue

            author = message.get("author") or {}
            role = self._safe_text(author.get("role")).lower() or "assistant"

            content = message.get("content") or {}
            text = ""
            thinking = None
            blocks: List[Dict[str, str]] = []
            if isinstance(content, dict):
                blocks = self._extract_openai_message_parts(content)
                text, thinking = self._summarize_blocks(blocks)

            if not blocks:
                continue

            ts = self._as_float(message.get("create_time"))
            order_ts = ts if ts is not None else float("inf")
            extracted.append((order_ts, Message(role=role, text=text, sent_ts=ts, thinking_text=thinking, blocks=blocks)))
            if ts is not None:
                timestamps.append(ts)

        extracted.sort(key=lambda pair: pair[0])
        messages = [m for _, m in extracted]
        if not messages:
            return None

        first_ts = min(timestamps) if timestamps else self._as_float(raw.get("create_time"))
        last_ts = max(timestamps) if timestamps else self._as_float(raw.get("update_time"))

        title = self._safe_text(raw.get("title")) or "Untitled OpenAI Chat"
        conversation_id = f"openai|{source_file.as_posix()}|{idx}"

        return Conversation(
            id=conversation_id,
            provider="openai",
            title=title,
            source_file=source_file.as_posix(),
            first_ts=first_ts,
            last_ts=last_ts,
            model_name=None,
            messages=messages,
        )

    def _from_anthropic(self, raw: Dict[str, Any], source_file: Path, idx: int) -> Optional[Conversation]:
        raw_messages = raw.get("chat_messages")
        if not isinstance(raw_messages, list):
            return None

        sortable: List[Tuple[float, Message]] = []
        timestamps: List[float] = []

        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            sender = self._safe_text(item.get("sender")).lower()
            role = "user" if sender == "human" else "assistant"
            blocks = self._extract_anthropic_blocks(item)
            text, thinking = self._summarize_blocks(blocks)

            if not blocks:
                continue

            ts = self._parse_iso_datetime(item.get("created_at"))
            order_ts = ts if ts is not None else float("inf")
            sortable.append((order_ts, Message(role=role, text=text, sent_ts=ts, thinking_text=thinking, blocks=blocks)))
            if ts is not None:
                timestamps.append(ts)

        sortable.sort(key=lambda pair: pair[0])
        messages = [m for _, m in sortable]
        if not messages:
            return None

        first_ts = min(timestamps) if timestamps else self._parse_iso_datetime(raw.get("created_at"))
        last_ts = max(timestamps) if timestamps else self._parse_iso_datetime(raw.get("updated_at"))

        title = self._safe_text(raw.get("name")) or "Untitled Anthropic Chat"
        conversation_id = f"anthropic|{source_file.as_posix()}|{idx}"

        return Conversation(
            id=conversation_id,
            provider="anthropic",
            title=title,
            source_file=source_file.as_posix(),
            first_ts=first_ts,
            last_ts=last_ts,
            model_name=None,
            messages=messages,
        )

    def _from_lmstudio(self, raw_conv: Dict[str, Any], source_file: Path) -> Optional[Conversation]:
        messages_raw = raw_conv.get("messages")
        if not isinstance(messages_raw, list):
            return None

        messages: List[Message] = []
        for item in messages_raw:
            if not isinstance(item, dict):
                continue

            role, blocks = self._extract_lmstudio_message_blocks(item)
            compact_text, thinking_text = self._summarize_blocks(blocks)

            sent_ts = None
            if blocks:
                messages.append(
                    Message(role=role, text=compact_text, sent_ts=sent_ts, thinking_text=thinking_text, blocks=blocks)
                )

        if not messages:
            return None

        model_info = raw_conv.get("lastUsedModel") if isinstance(raw_conv, dict) else None
        model_name = None
        if isinstance(model_info, dict):
            model_name = self._safe_text(model_info.get("title")) or self._safe_text(model_info.get("identifier")) or None

        title = self._safe_text(raw_conv.get("name"))
        if not title:
            first_user = next((m.text for m in messages if m.role == "user" and m.text), "")
            title = (first_user[:60] + "...") if len(first_user) > 63 else first_user
        if not title:
            title = "Untitled LM Studio Chat"

        created_ts = self._parse_any_datetime(raw_conv.get("createdAt")) or self._parse_any_datetime(raw_conv.get("created_at"))
        updated_ts = self._parse_any_datetime(raw_conv.get("updatedAt")) or self._parse_any_datetime(raw_conv.get("updated_at"))

        file_mtime = source_file.stat().st_mtime if source_file.exists() else None
        first_ts = created_ts or file_mtime
        last_ts = updated_ts or file_mtime or created_ts

        conversation_id = f"lmstudio|{source_file.as_posix()}"

        return Conversation(
            id=conversation_id,
            provider="lmstudio",
            title=title,
            source_file=source_file.as_posix(),
            first_ts=first_ts,
            last_ts=last_ts,
            model_name=model_name,
            messages=messages,
        )


STORE = TranscriptStore()
SETTINGS = SettingsStore(SETTINGS_PATH)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "LocalTranscriptViewer/1.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/conversations":
            self._handle_list(query)
            return

        if path == "/api/conversation":
            self._handle_detail(query)
            return

        if path == "/api/settings":
            self._handle_settings_get()
            return

        if path == "/" or path == "":
            self._serve_static("index.html")
            return

        self._serve_static(path.lstrip("/"))

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/settings":
            payload = self._read_json_body()
            if payload is None:
                self._send_json({"error": "Invalid JSON payload"}, status=400)
                return
            self._handle_settings_update(payload)
            return

        if path == "/api/pick-folder":
            payload = self._read_json_body()
            if payload is None:
                payload = {}
            self._handle_pick_folder(payload)
            return

        self.send_error(404, "Not found")

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length_text = self.headers.get("Content-Length")
        if not length_text:
            return {}

        try:
            length = int(length_text)
        except ValueError:
            return None

        try:
            raw = self.rfile.read(length)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None

        if isinstance(data, dict):
            return data
        return None

    def _handle_list(self, query: Dict[str, List[str]]) -> None:
        root_param = (query.get("root") or [""])[0].strip()
        lm_root_param = (query.get("lm_root") or [""])[0].strip()
        force = (query.get("force") or ["0"])[0] == "1"
        settings = SETTINGS.get()

        if root_param:
            root = Path(root_param).expanduser()
        else:
            root = Path(settings.transcript_root)

        if lm_root_param:
            lm_root = Path(lm_root_param).expanduser()
        else:
            lm_root = _effective_lmstudio_root(settings.lmstudio_root)

        if not root.exists() or not root.is_dir():
            self._send_json({"error": "Invalid root folder"}, status=400)
            return

        STORE.scan(root=root, lm_root=lm_root, force=force)
        conversations = STORE.list_metadata()

        provider_counts: Dict[str, int] = {"openai": 0, "anthropic": 0, "lmstudio": 0}
        for conv in conversations:
            provider = str(conv.get("provider", ""))
            provider_counts[provider] = provider_counts.get(provider, 0) + 1

        self._send_json(
            {
                "root": root.as_posix(),
                "lm_root": lm_root.as_posix(),
                "provider_counts": provider_counts,
                "conversations": conversations,
            }
        )

    def _handle_settings_get(self) -> None:
        settings = SETTINGS.get()
        lm_root = _effective_lmstudio_root(settings.lmstudio_root)
        self._send_json(
            {
                "transcript_root": settings.transcript_root,
                "lmstudio_root": lm_root.as_posix(),
            }
        )

    def _handle_settings_update(self, payload: Dict[str, Any]) -> None:
        transcript_root = payload.get("transcript_root")
        lmstudio_root = payload.get("lmstudio_root")

        updated = SETTINGS.update(
            transcript_root=transcript_root if isinstance(transcript_root, str) else None,
            lmstudio_root=lmstudio_root if isinstance(lmstudio_root, str) else None,
        )
        self._send_json(
            {
                "transcript_root": updated.transcript_root,
                "lmstudio_root": updated.lmstudio_root,
            }
        )

    def _handle_pick_folder(self, payload: Dict[str, Any]) -> None:
        target = str(payload.get("target") or "").strip().lower()
        settings = SETTINGS.get()
        initial = settings.transcript_root if target == "transcript_root" else settings.lmstudio_root

        selected = None
        picker_errors: List[str] = []

        try:
            from tkinter import Tk, filedialog

            root = Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(initialdir=initial or str(Path.home()))
            root.destroy()
        except Exception as exc:
            picker_errors.append(f"tkinter picker failed: {exc}")

        if not selected and os.name == "nt":
            selected, ps_error = _pick_folder_windows_powershell(initial or str(Path.home()))
            if ps_error:
                picker_errors.append(ps_error)

        if not selected and picker_errors:
            self._send_json({"error": "Unable to open folder picker", "details": picker_errors}, status=500)
            return

        if not selected:
            self._send_json({"selected": None})
            return

        self._send_json({"selected": _normalize_path_text(selected)})

    def _handle_detail(self, query: Dict[str, List[str]]) -> None:
        conversation_id = (query.get("id") or [""])[0]
        if not conversation_id:
            self._send_json({"error": "Missing id"}, status=400)
            return

        conversation = STORE.get_conversation(conversation_id)
        if not conversation:
            self._send_json({"error": "Conversation not found"}, status=404)
            return

        self._send_json(conversation)

    def _serve_static(self, relative: str) -> None:
        if ".." in relative.replace("\\", "/"):
            self.send_error(400, "Invalid path")
            return

        target = WEB_DIR / relative
        if not target.exists() or not target.is_file():
            self.send_error(404, "Not found")
            return

        suffix = target.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")

        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def run_server(host: str, port: int, open_browser: bool) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    url = f"http://{host}:{port}"
    print(f"Local Transcript Viewer running at {url}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local transcript viewer web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
