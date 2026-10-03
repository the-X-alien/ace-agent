# Ace

Ace is a free, open-source harness layer for the AI providers you already use. You bring your own provider and key. Ace adds three things on top:

1. **Automatic skills.** It reads your prompt and picks the specialist working rules that fit (web design, Python, testing, writing, data analysis, security review). It shows you which words caused each pick.
2. **Output checks.** It checks the reply (markup, syntax, placeholder text), and asks the model to repair hard failures once.
3. **One table for humans and agents.** A shared board where people and agents take tasks, post live progress, share context, and a human approves before anything is marked done.

Status: **v0.1.5, early.** Everything below says what exists today. Speed, cost and quality improvements are goals to be measured, not results. `ace compare` exists so you can measure them yourself on your own provider.

## Install (v0.1.5)

Needs Python 3.9 or newer (on Windows the installer adds Python 3.12 for you with winget if it is missing). No git, no pipx, no admin rights.

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.5/scripts/install.ps1 | iex
```

**macOS and Linux (Terminal):**

```sh
curl -fsSL https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.5/scripts/install.sh | sh
```

Then check it and start it in your project folder:

```sh
ace-agent --version   # prints 0.1.5
ace-agent             # full-screen terminal UI
```

- Use `ace-agent`. The short `ace` command is also installed unless another program on your PC already uses that name; the installer tells you and never touches it.
- The installer makes its own private Python environment, adds the command to your user PATH once, and runs a version check. Reopen an already open terminal on macOS and Linux.
- Update later with `ace-agent update` (it rolls back if the new version fails to start).
- Remove it: Windows `$env:ACE_UNINSTALL="1"; irm <same url> | iex`, macOS/Linux `curl -fsSL <same url> | ACE_UNINSTALL=1 sh`.
- Tested: the pinned one-liners run green on GitHub's Windows, Ubuntu and macOS runners ([oneliner-check](https://github.com/the-X-alien/ace-agent/actions/workflows/oneliner.yml)), and the Windows install/update and unit tests pass on a Windows runner. Not yet tested: an interactive Windows console by a person, and any real model.
- Read the scripts first if you like: `scripts/install.sh` and `scripts/install.ps1`.

Project data lives in each project's `.ace/` folder.

## Agent mode (new, early)

Run `ace-agent` with no arguments in a terminal for the full-screen UI (type `/` for commands, Ctrl+P for the palette; a plain line chat is used when output is not a terminal): `/model`, `/sessions`, `/resume`, `/yes-edits`, `/help`.

    ace-agent agent "fix the failing test in tests/test_math.py" --provider openai

The model can list and read files, search, write and edit files, and run shell commands, only inside the project folder. Every write, edit and command asks you first. This is permission prompting, not a sandbox: file tools are confined to the project folder, but an approved shell command runs with your user rights and can reach anything you can, so read commands before you say yes; `--auto-edit` and `--allow-shell` skip the question. Without a terminal to ask in, edits and commands are refused. Sessions are saved in `.ace/sessions/` and resume with `--resume <id>`; `ace-agent sessions` lists them. Tool calls travel as plain text in a fixed format, so any provider can in principle be used, but the model has to follow that format; only scripted fake models have been tested so far. The default `mock` provider cannot use tools: pick a real one. Tested with scripted fake models on Linux and the Windows runner; not yet measured against real models, and not yet compared with other agents. No claims about speed or quality.

## Build a website from one prompt (new, early)

```sh
ace-agent site "A cozy neighbourhood coffee shop called Bean There in Portland" --provider lmstudio
```

Writes `index.html`. Ace asks the model for the content in five short steps (name and tagline, about, offerings with prices, sample reviews, contact), retries unusable answers, assembles the page and runs its HTML check. The words come from the model; the layout and CSS come from Ace's built-in template, so this is not a free-form coding agent. It works with small local models: it was run with Qwen2.5-Coder-1.5B (llama.cpp) on a 2-CPU machine, about 90 seconds per page. Details such as addresses, hours and reviews are invented by the model and the reviews are labelled as samples. Set `"max_tokens"` on an openai-compatible provider in `.ace/config.json` to stop runaway replies. Needs a configured provider; the default mock cannot do it.

What is and is not covered from the team's feature list: [docs/COVERAGE.md](docs/COVERAGE.md).

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

## Credits

Ace is MIT licensed. Ideas and prior art that shaped it, no code copied so far: [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (MIT, Copyright (c) 2026 diegosouzapw) for provider routing, fallback and light prompt-trimming ideas such as collapsing whitespace and capping tool-result length; [OpenCode](https://opencode.ai) for the feature set that agent mode is measured against. If code is copied later, its licence text goes in a NOTICE file.

Updates: `ace-agent update` downloads the release from this repository over HTTPS and rolls back if the new version does not start. Release archives are not signed and have no pinned checksum, so this is not verified publisher authentication.
