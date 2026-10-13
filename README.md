# Marginalia

Ask questions across your own documents (PDFs, text files, pasted notes, web links) and see exactly which passage each answer comes from. Matching words are highlighted like a highlighter pen, and every claim in an answer links back to its source.

**How it works:** documents are split into passages, ranked against your question with BM25 (a classic search-ranking algorithm), and the top passages are sent to Claude, which writes a cited answer. Without an API key it still works and shows the best-matching passages.

**Tech:** Python standard library web server, `pypdf` for PDFs, plain HTML/CSS/JavaScript. One dependency, no build step, no Anaconda.

## Run it on a Mac

1. Open **Terminal** (press Cmd+Space, type `Terminal`, press Enter).
2. Check Python: `python3 --version`. You need **3.9 or newer**. If it is missing or older, install the latest from https://www.python.org/downloads/macos/ , then open a new Terminal window.
   (If macOS offers to install "command line developer tools", click Install, wait, then try again.)
3. Go to the project folder: type `cd ` (with a space), drag the `marginalia` folder into the Terminal window, press Enter.
4. Start it: `bash run.sh`

The first run creates a private `.venv` folder and installs `pypdf`. Your browser opens at http://127.0.0.1:8000. Stop the app with Ctrl+C. Next time, repeat steps 3 and 4.

## Get written answers from Claude (optional)

1. Create a key at https://console.anthropic.com/ (API usage is billed separately from a Claude chat subscription).
2. In the project folder run `cp .env.example .env`, then open `.env` and paste the key after `ANTHROPIC_API_KEY=`.
3. Restart with `bash run.sh`. The terminal shows `Claude answers: ON`.

With a key on, your question and the matching passages are sent to Anthropic to write the answer. Never commit `.env` (it is already in `.gitignore`).

## Put it on GitHub

1. Create a free account at https://github.com, click **New repository**, name it `marginalia`, leave "Add a README" **unticked**, click **Create repository**.
2. In Terminal, inside the project folder (replace `YOUR-USERNAME`):

```bash
git init
git add .
git commit -m "Initial commit: Marginalia document assistant"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/marginalia.git
git push -u origin main
```

When asked for a password, paste a **Personal Access Token** (GitHub: Settings > Developer settings > Personal access tokens > Tokens (classic) > tick `repo`). Your normal password will not work.
If `git` asks to install developer tools, accept, then repeat.

## Run the tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

## Troubleshooting

| You see | Do this |
| --- | --- |
| `python3: command not found` | Install Python from python.org (step 2 above). |
| `Python 3.9 or newer is required` | Install a newer Python from python.org, open a new Terminal. |
| `xcrun: error: invalid active developer path` | Run `xcode-select --install`, wait, retry. |
| Port 8000 is busy | Nothing: the app tries the next ports and prints the right address. |
| "No readable text found" for a PDF | The PDF is a scan (images). Run it through OCR first. |
| pip fails to download | Check your internet connection and run `bash run.sh` again. |

## Make it public later

Hosts such as Render or Railway can run it: build command `pip install -r requirements.txt`, start command `HOST=0.0.0.0 python server.py`, and add `ANTHROPIC_API_KEY` as a secret. Before opening it to the public, add user accounts: right now everyone who can reach the site shares one library.

## Ideas to extend it (good portfolio upgrades)

- Add embedding-based search (for example `fastembed`) and combine it with BM25 for hybrid retrieval.
- Add a small evaluation set (questions with known answers) and report retrieval accuracy.
- Add a cross-encoder re-ranker, OCR for scanned PDFs, and per-user libraries.

## Project layout

```
server.py        web server and API
app/rag.py       extraction, chunking, BM25 search, library storage
app/llm.py       Claude API call and fallback
static/          the web page (HTML, CSS, JS)
tests/           automated tests
data/            your library is saved here (not committed)
```
