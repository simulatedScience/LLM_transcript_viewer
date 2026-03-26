# Simple Transcript Viewer

A local LLM transcript viewer with a modern web UI for browsing OpenAI, Anthropic, and LM Studio chat exports.

## Features

- Scans a root folder recursively for provider exports.
- Supports:
  - OpenAI exports from `conversations.json` (`mapping` schema)
  - Anthropic exports from `conversations.json` (`chat_messages` schema)
  - LM Studio exports from `.lmstudio/conversations` (`*.conversation.json`)
- Provider icons in the sidebar (OpenAI, Anthropic, LM Studio).
- Sidebar search and fast lazy chat loading.
- Clear sort presets (newest/oldest activity, newest/oldest start, title A-Z/Z-A).
- Provider filter toggles (one per provider).
- Main panel with distinct user/model/system chat bubbles.
- Optional markdown rendering in both message text and thinking blocks.
- Persistent path settings with local folder picker (saved to `viewer_settings.json`).

## Run (Web UI)

```powershell
cd simple_transcript_viewer
python web_viewer.py
```

Optional:

```powershell
python web_viewer.py --host 127.0.0.1 --port 8765 --no-browser
```

## Notes

- Path settings live in the `Path Settings` section at the bottom of the left panel.
- Use `Browse` to open a native folder picker for transcript and LM Studio directories.
- Click `Save Paths` once; the app persists settings in `simple_transcript_viewer/viewer_settings.json`.
- Large exports may take a few seconds on first scan.
