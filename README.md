# Ace

Ace is a free, open-source harness layer for the AI providers you already use. You bring your own provider and key. Ace adds three things on top:

1. **Automatic skills.** It reads your prompt and picks the specialist working rules that fit (web design, Python, testing, writing, data analysis, security review). It shows you which words caused each pick.
2. **Output checks.** It checks the reply (markup, syntax, placeholder text), and asks the model to repair hard failures once.
3. **One table for humans and agents.** A shared board where people and agents take tasks, post live progress, share context, and a human approves before anything is marked done.

Status: **v0.1.1, early.** Everything below says what exists today. Speed, cost and quality improvements are goals to be measured, not results. `ace compare` exists so you can measure them yourself on your own provider.

## Install

Needs Python 3.9 or newer. No other dependencies.

One line (pinned to v0.1.3). Needs Python 3.9+; no git, no pipx, no admin rights.

macOS and Linux (Terminal):

    curl -fsSL https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.3/scripts/install.sh | sh

Windows (PowerShell):

    irm https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.3/scripts/install.ps1 | iex

Then type `ace-agent doctor` (`ace` works too, unless another program on your PC already uses that name; the installer tells you and never touches it). The installer sets up its own private Python environment, puts `ace` on your PATH for this window and your future terminals (your user PATH only, added once), and starts `ace --version` to check itself. On Windows, if Python is missing it installs Python 3.12 for your user with winget. On macOS and Linux a terminal that was already open needs to be reopened.

Remove it: Windows `$env:ACE_UNINSTALL="1"; irm <same url> | iex`, macOS/Linux `curl -fsSL <same url> | ACE_UNINSTALL=1 sh`.

Read the scripts first if you like: scripts/install.sh and scripts/install.ps1.

Project data lives in each project's `.ace/` folder.

## Quick start

    ace init                      # creates .ace/config.json
    ace doctor                    # shows which providers are ready
    ace pick "build a responsive landing page in html"
    ace run "build a responsive landing page in html" --out page.html
    ace compare "build a responsive landing page in html" --report compare.html
    ace board                     # shared board at http://localhost:8765

Edit `.ace/config.json` to choose a provider and set its model. Keys are read from environment variables (for example `OPENAI_API_KEY`) and are never written to disk by Ace.

Provider types: `openai-compatible` (OpenAI, OpenRouter, a local Ollama or LM Studio, or any compatible gateway including OmniRoute), `anthropic`, `cli` (any agent command-line tool that takes a prompt), and `echo`.

The default provider is `mock` (type `echo`): it returns canned text so you can try the commands offline. Its output is labelled MOCK and says nothing about quality. Switch `default_provider` to a real one for real work.

## The board

`ace board` serves a small web app. Add tasks, assign them to a person or an agent, mark tasks that need human approval, watch progress update live, and keep shared context notes. Agents can take a task with `ace work <id>`: it reads the task and the shared notes, runs the prompt through Ace, posts progress, attaches the result, and moves the task to review (or done if no approval is needed). Only a human actor can approve.

Two modes:

- **Open mode (default, localhost only).** Each request says whether the actor is a human or an agent, and the server believes it. The approval gate is a workflow step that stops honest agents and honest mistakes. It is **not a security boundary**: any local process can claim to be a human.
- **Secure mode (`ace board --secure`, forced when `--host` is not localhost).** Ace prints two different tokens, one for humans and one for agents. The server decides human vs agent from the token, so a client holding only the agent token cannot approve, even if it says it is a human. A test covers this. Names are still self-reported, so this separates people from agents, not one person from another. Traffic is plain HTTP, so use it on a network you trust. It is not hardened for the open internet.

## Honest status

- Tested: 33 unit tests (skills, checks, provider adapters against a local fake server, the board and approval gate, the CLI). Run on Linux so far. The CI workflow in `.github/workflows` is manual only (run it from the Actions tab) and has not been run yet, so macOS and Windows are untested.
- Not yet tested against a real model provider or a real agent CLI (`claude`, `codex`). The adapters follow the documented request shapes, but treat them as unverified until you run `ace run` with your own key.
- Skill selection is a transparent keyword score. It has not been benchmarked.
- The output checks are structural. They do not judge design quality or factual accuracy.
- iMessage texting through Photon is not built. `ace connectors` reports it as not configured.
- Planned, not built: automatic weekly self-improvement from web research. If built, it will only propose changes for a human to approve, because web text is untrusted input.

## Develop

    python -m unittest discover -s tests -v

MIT licensed. See `LICENSE`.
