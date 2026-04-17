# Installing the CutMaster panel inside Resolve

This directory is a Workflow Integration Plugin. It loads the locally-running
Python backend (`celavii-resolve-panel`) in a Resolve dock panel.

## 1. Start the Python backend

```bash
celavii-resolve-panel
# → http://127.0.0.1:8765
```

Keep this running in the background whenever you use the panel.

## 2. Install the plugin folder

Copy **this entire `resolve-plugin/` directory** (not just individual files)
into Resolve's Workflow Integration Plugins folder, renaming it to
`CelaviiCutMaster`.

**macOS**

```bash
cp -r panel/resolve-plugin \
  ~/Library/Application\ Support/Blackmagic\ Design/DaVinci\ Resolve/Fusion/Workflow\ Integration\ Plugins/CelaviiCutMaster
```

**Windows**

```powershell
xcopy /E panel\resolve-plugin `
  "%APPDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Workflow Integration Plugins\CelaviiCutMaster\"
```

## 3. Enable developer mode (first time only)

Resolve only loads third-party Workflow Integration plugins if developer mode
is on:

1. Open Resolve.
2. `Workspace → Console` (macOS: `⌥⇧⌘ C`).
3. Paste into the Lua tab:
   ```lua
   resolve:SetDeveloperMode(true)
   ```
4. Restart Resolve.

## 4. Open the panel

`Workspace → Workflow Integrations → CutMaster AI`

## If it doesn't load

The XML schema in `manifest.xml` is based on the public documentation; Resolve
may have tweaked it between versions. If your panel doesn't appear:

1. Check Resolve's built-in sample plugins for the current `manifest.xml`
   format (see the path in `manifest.xml` comments).
2. Copy the tag names from a working example.
3. Restart Resolve after any manifest change.

As a fallback during development, just open `http://127.0.0.1:8765/` in
Safari or Chrome — same UI, no Workflow Integration needed.
