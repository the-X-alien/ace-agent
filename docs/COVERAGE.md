# Feature coverage (honest record)

Source: the team's feature Doc "Agent harness features: best capabilities from each project" (about 450 catalogued projects in four sections: coding agents and harnesses, orchestrators and multi-agent managers, other entries, and entries judged not to be harnesses). The Doc lists what each project's authors claim. Nothing in it was run or benchmarked by us.

**Ace does not have all the features of all the other harnesses.** It has a small working core, listed below. Everything else is not built. This file is updated as that changes.

## Your additions (Dhiaan and Neal), status today

| Requirement from the Doc | Status |
|---|---|
| iMessage texting through Photon | Not built. A stub in `ace/connectors.py` reports that. What "Photon" means is unconfirmed. |
| Should be faster | Not measured. No speed claim. |
| Better design | Partly: OpenCode-style full-screen terminal UI (slash popup, Ctrl+P palette, markdown and diff rendering). Checked in a Linux pty and on a Windows CI runner; not by a person on a real Windows console. |
| Not slop | Partly: output checks (HTML, Python, JSON, placeholder text) with one repair pass; `write_file` of HTML feeds check failures back to the model. |
| Working | Partly: a real local model (Qwen2.5-Coder 1.5B through llama.cpp) has driven `ace-agent site` to a full page and `ace-agent agent` to a tiny page. Free-form coding quality with a small model is poor. |
| Score high on a benchmark | Not run. No Terminal-Bench or coffee-shop comparison with OpenCode yet. |
| Increaseable context size | Partly: set `"context_chars"` on a provider in `.ace/config.json` (and `max_tokens`) and the agent drops the oldest turns to fit; it counts characters, not tokens, and does not detect the model's real window. Unit-tested only. |
| Automatic tool selector | Partly: Ace picks working-rule skills from the prompt (`ace pick`). It does not choose among tools. |
| New architecture? | Open question, nothing decided. |
| Multiple people connectivity; AI + human + another AI + another human working at once | Partly: the shared board (`ace board`) lets people and agents take tasks, post progress and share notes, human approval gate. Not tested with many users. |
| Can connect to a company's context | Not built. |
| Agent-to-agent connectivity | Partly: agents take tasks from the board and post results. No direct agent messaging. |
| Collaborate with agents like Claude or Codex | Provider entries `claude-cli` and `codex-cli` exist. Never run. |
| A unique, iconic feature | Not chosen. |
| Automatic self-improvement (weekly web scan for new projects, papers, techniques) | Not built. |

## Harness features from the catalogue

Built (small versions, own code): file list/read/search/write/edit and shell tools with approval prompts, saved sessions and resume, provider switching (`/model`), full-screen TUI, Ctrl+P commands, update with rollback, local-model providers (LM Studio, Ollama, llama.cpp server), shared board.

Not built (examples named in the Doc): git-native auto-commit and undo (Aider), Docker sandboxes (OpenHands and others), memory graphs and side-agents (jcode), MCP servers, LSP, sub-agents and parallel worktrees, plugins, themes, native tool calling, repo maps, IDE integrations. The shell runs with permission prompts, not OS isolation.

No catalogued project's code has been copied. OmniRoute (MIT) was only read for ideas.

## OpenCode terminal UI: element-by-element (compared with OpenCode 1.18.34 run in a pty and screenshotted)

Ace's UI is its own code, made to look similar. Checked only in a Linux pty with a scripted fake model, and on a Windows CI runner by unit tests (no person has used it in a real Windows console).

| OpenCode element | Ace |
|---|---|
| Centered block logo on the home screen | Yes (ACE logo) |
| Dark prompt panel with blue left bar and "Build · model" line | Yes |
| Slash-command popup with highlighted selected row | Yes, 9 commands (/help /model /sessions /resume /new /yes-edits /yes-shell /ask /exit) vs OpenCode's longer list (/agents /connect /diff /init /mcps and more, not built) |
| Ctrl+P commands palette with search and esc | Yes, but one flat list (no Suggested/System sections, no "Switch theme" and similar entries) |
| "tab agents / ctrl+p commands" hint under prompt | Partly: Tab toggles two modes, Build and Plan (Plan = read-only tools, `/plan` `/build`); OpenCode's other agents are not built |
| User message panels, dim tool lines | Yes |
| Right sidebar with session info (110+ columns) | Yes, simplified |
| Footer with path and version | Yes |
| Markdown rendering (headings, bold, code fences) | Basic (headings, bold, inline code, bullets, fences), no syntax highlighting, no tables |
| Diff view for edits | Only +/- colouring of diff code blocks the model writes; no real file diff viewer |
| Session list/switcher UI, themes, light mode, mouse support, agent (Tab) switching, plugins, MCP view, status view | Not built |

## Providers: what has actually been tested

Test levels: **real** = a real model answered through Ace; **stub** = request shape and parsing checked against a local fake server (proves nothing about the hosted service); **config only** = entry exists, never run.

| Provider entry | Level |
|---|---|
| openai-compatible against a local llama.cpp server (Qwen2.5-Coder-1.5B) | real (Linux, my test machine; `site`, `agent` and `ping` all ran) |
| openai-compatible, generic (headers, path, max_tokens, errors) | stub |
| openai, openrouter, featherless, lmstudio, ollama | config only (same code path as the stub-tested adapter; never called) |
| anthropic | stub (headers, path, response parsing) |
| claude-cli, codex-cli | stub with a fake command (argument and stdin modes, failure); the real CLIs were never run |
| mock (echo) | offline, canned output, proves nothing |

`ace-agent ping --provider NAME` sends one real tiny request, so you can check any provider you configure yourself. Hosted providers have their own terms and age rules; read them before creating an account.
