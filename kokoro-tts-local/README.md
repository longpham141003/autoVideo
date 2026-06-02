# Kokoro TTS Local

Small local wrapper for Kokoro text-to-speech. It uses the official
`kokoro` Python package and writes 24 kHz audio files.

Source references:

- GitHub: https://github.com/hexgrad/kokoro
- Model: https://huggingface.co/hexgrad/Kokoro-82M

## Notes

- The first run downloads model/voice files from Hugging Face. After that,
  cached files can be reused offline.
- Kokoro does not currently provide a native Vietnamese voice. English works
  best. Supported language codes in the official examples include:
  `a` American English, `b` British English, `e` Spanish, `f` French,
  `h` Hindi, `i` Italian, `j` Japanese, `p` Brazilian Portuguese, `z` Mandarin.
- On Windows, install `espeak-ng` if pronunciation fallback or non-English
  language support fails.

## Setup

From this folder:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
```

## Quick use

Web UI:

```powershell
.\start_ui.ps1
```

Then open:

```text
http://127.0.0.1:7860
```

Use `Nghe thu giong dang chon` to preview the selected voice before generating
your full text.

```powershell
.\.venv\Scripts\python.exe .\tts.py --text "Hello, this is Kokoro running locally." --out outputs\hello.wav
```

Short PowerShell wrapper:

```powershell
.\speak.ps1 -Text "Hello, this is Kokoro running locally." -Out outputs\hello.wav
```

From a text file:

```powershell
.\.venv\Scripts\python.exe .\tts.py --file input.txt --out outputs\speech.wav
```

Change voice/language:

```powershell
.\.venv\Scripts\python.exe .\tts.py --lang b --voice bf_alice --text "A British voice sample." --out outputs\british.wav
```

Slow down or speed up:

```powershell
.\.venv\Scripts\python.exe .\tts.py --speed 0.9 --text "Slower speech." --out outputs\slow.wav
```
