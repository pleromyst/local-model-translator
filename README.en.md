# Local Model Translator

[中文](README.md) | English

This is an experimental project. It can't compete with WeChat's built-in screenshot translation, and using a local model turned out to be more flashy than practical. Its only real strengths are fully offline use and translating somewhat longer text—and both depend on your local model. It clearly didn't meet my expectations. It's just a little toy, but you're welcome to give it a try.

This is the final version. I don't currently plan to keep improving it.

The app is called **LocalLens**. It runs on Windows, translates with a local AI model through Ollama, and reads screenshot text with RapidOCR. Download your model first, then use it offline.

## Screenshots

![Main window](docs/images/main-window.png)

![Translation popup](docs/images/translation-example.png)

The popup has rounded corners and a translucent background.

## Get started

**[Download the Windows app](https://github.com/pleromyst/local-model-translator/releases/latest)**: under Assets, download `LocalLens-Windows-x64.zip`. Right-click it, choose “Extract All,” open the extracted `LocalLens` folder, and run `LocalLens.exe`. You don't need the Source code downloads.

1. Use 64-bit Windows 10 / 11. Install and start [Ollama](https://ollama.com/), then download a model you want to use.
2. For a packaged build, extract the whole folder and run `LocalLens.exe`. No Python needed. For GitHub's source ZIP, follow the source instructions below.
3. Choose your model in Settings and save. Run `ollama list` to check model names; replace the default `deepseek-r1:8b` with yours. The Ollama address can usually stay at `http://127.0.0.1:11434`.

## Translate

- **Selected text:** Hold the left mouse button and drag to select text, then press `Alt+T`.
- **Screenshot:** Press `Alt+Q` and draw a box around the text. Press `Esc` to cancel.
- **Popup:** Drag it around or click `Copy` to copy the translation.
- **Languages:** English becomes Chinese, and Chinese becomes English. Mixed Chinese and English stays unchanged without calling the model.
- **Layout:** Screenshot text is joined into one line, keeping recognized punctuation. Long text is translated in parts and put back together in order.

Each translation is a fresh request, and only the translation is shown. Change shortcuts and models in Settings. The model is unloaded after each translation by default, so loading it next time may take a little longer.

Try not to use it while gaming—you know how running a local model eats up your computer's resources.

## Start and quit

The packaged app adds a Start menu entry on its first run and defaults to starting in the background when you sign in to Windows. No desktop shortcut. Use the hotkeys directly, or left-click the tray icon to open the main window.

Closing the window keeps it running. Quit fully from the tray menu, then search for `LocalLens` in Start to reopen it. Turn off `Sign-in startup` in Settings to disable automatic startup. If you move the app folder, run it once to update the paths.

## A few notes

- Busy backgrounds, small text, and blurry images can cause recognition errors. Missing punctuation isn't added automatically.
- Some fullscreen games and protected content can't be captured. If selecting text in an administrator app fails, try a screenshot.
- Multiple monitors and mixed display scaling may have compatibility issues.
- By default, text and screenshots aren't uploaded, and translation history isn't saved. Download your model ahead of time.
- Settings are in `config/settings.json`. Logs rotate automatically in `logs/locallens.log` without recording source text or translations. Debug screenshots are only saved when Debug is enabled.

## Run or build from source

Open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Run tests with `.\.venv\Scripts\python.exe -m pytest -q`. Use `requirements-lock.txt` instead of `requirements.txt` for pinned dependency versions.

Build:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1
```

Share the entire `dist\LocalLens` folder, not just the EXE.

## License

[MIT](LICENSE). Feel free to use it, learn from it, or change it.
