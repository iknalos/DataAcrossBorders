# Pre-Hackathon Setup Guide

Complete these steps **before** the hackathon so you're ready to code on day one. Budget about 15–20 minutes.

---

## 1. Install Python 3.10+

You need Python 3.10 or newer. Check if you already have it:

```bash
python3 --version
```

If you see `Python 3.10.x` or higher, you're good — skip to Step 2.

### macOS

Option A — Official installer (simplest):
1. Download from https://www.python.org/downloads/
2. Run the `.pkg` installer
3. Verify: `python3 --version`

Option B — Homebrew:
```bash
brew install python
```

### Windows

1. Download from https://www.python.org/downloads/
2. Run the installer — **check "Add Python to PATH"** (important!)
3. Open a new Command Prompt or PowerShell and verify: `python --version`

> **Windows note:** On Windows, use `python` instead of `python3` for all commands in this guide.

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

---

## 2. Install Git

Check if you already have it:

```bash
git --version
```

### macOS

Git comes with Xcode Command Line Tools:
```bash
xcode-select --install
```

Or via Homebrew: `brew install git`

### Windows

Download from https://git-scm.com/download/win and run the installer. Use the default settings.

### Linux

```bash
sudo apt install git
```

---

## 3. Install a Code Editor

We recommend **Visual Studio Code** (free): https://code.visualstudio.com/

Helpful VS Code extensions to install:
- **Python** (by Microsoft) — syntax highlighting, linting, debugging
- **REST Client** or **Thunder Client** — test API endpoints without leaving the editor

Any editor works (PyCharm, Sublime Text, vim, etc.), but VS Code has the smoothest Python experience out of the box.

---

## 4. Clone the Repository

```bash
git clone <REPO_URL>
cd hospital-node-boilerplate
```

*(The repo URL will be shared at the hackathon or via your team channel.)*

---

## 5. Create a Virtual Environment (Recommended)

A virtual environment keeps this project's packages separate from your system Python.

### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows (Command Prompt)

```bash
python -m venv venv
venv\Scripts\activate
```

### Windows (PowerShell)

```bash
python -m venv venv
venv\Scripts\Activate.ps1
```

> You'll see `(venv)` at the start of your terminal prompt when the environment is active.

---

## 6. Install Project Dependencies

With your virtual environment activated:

```bash
pip install -r requirements.txt
```

This installs:
- **FastAPI** — the web framework powering each hospital node
- **Uvicorn** — the ASGI server that runs the app
- **Pydantic** — data validation and serialization

---

## 7. Verify Everything Works

Start a single hospital node to confirm your setup:

```bash
HOSPITAL_NODE=BCH uvicorn main:app --port 8001 --reload
```

On **Windows Command Prompt**, set the env var separately:
```bash
set HOSPITAL_NODE=BCH
uvicorn main:app --port 8001 --reload
```

On **Windows PowerShell**:
```bash
$env:HOSPITAL_NODE="BCH"
uvicorn main:app --port 8001 --reload
```

You should see output like:

```
INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)
INFO:     Started reloader process...
```

Open your browser to http://localhost:8001/health and you should see:

```json
{"status": "healthy", "node": "BCH"}
```

Try the interactive API docs at http://localhost:8001/docs — you can test all endpoints right from the browser.

Press `Ctrl+C` to stop the server when you're done.

---

## 8. Run All Three Nodes (Optional Pre-Check)

To simulate the full multi-hospital network, open **three separate terminal windows** and start one node in each:

```bash
# Terminal 1 — Boston Children's Hospital
HOSPITAL_NODE=BCH uvicorn main:app --port 8001 --reload

# Terminal 2 — Massachusetts General Hospital
HOSPITAL_NODE=MGH uvicorn main:app --port 8002 --reload

# Terminal 3 — Brigham and Women's Hospital
HOSPITAL_NODE=BWH uvicorn main:app --port 8003 --reload
```

Verify all three are running:

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
```

Or just open each URL in your browser.

---

## Troubleshooting

### `python3: command not found`
- **macOS:** Run `xcode-select --install`, then try again.
- **Windows:** Make sure you checked "Add Python to PATH" during installation. Reinstall if needed.
- **Linux:** Run `sudo apt install python3`.

### `pip: command not found`
Try `pip3` instead of `pip`. If that doesn't work:
```bash
python3 -m pip install -r requirements.txt
```

### `ModuleNotFoundError: No module named 'fastapi'`
You forgot to install dependencies, or your virtual environment isn't activated. Run:
```bash
source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### Port already in use
If you see `Address already in use`, another process is using that port. Either stop it or use a different port:
```bash
HOSPITAL_NODE=BCH uvicorn main:app --port 9001 --reload
```

### Windows: `uvicorn` not recognized
Make sure your virtual environment is activated (you should see `(venv)` in your prompt). If you installed Python to a custom path, you may need:
```bash
python -m uvicorn main:app --port 8001 --reload
```

---

## Checklist

Before the hackathon, confirm you can check all of these:

- [ ] `python3 --version` shows 3.10+
- [ ] `git --version` works
- [ ] Code editor installed
- [ ] Repository cloned
- [ ] `pip install -r requirements.txt` completed without errors
- [ ] `http://localhost:8001/health` returns `{"status": "healthy", "node": "BCH"}`

**You're ready to hack!**
