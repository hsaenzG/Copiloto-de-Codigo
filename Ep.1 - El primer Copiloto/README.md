# Code Copilot Agent

An AI code copilot that ingests a repository (GitHub URL or local path),
evaluates its quality, explains what it does, finds security vulnerabilities,
and recommends prioritized, actionable improvements.

Built with the **Strands Agents SDK** and **Amazon Bedrock (Amazon Nova Pro)**.
Runs in the terminal with **colored output**: the agent's natural-language
responses appear in one color, and each tool appears in its own color.

## Capabilities

The agent exposes nine tools:

| Tool | Purpose |
| --- | --- |
| `ingest_repository` | Clone (URL) or validate (local) a repo; return languages, files, size, package managers, config files, directory tree. |
| `analyze_structure` | Map modules, symbols, imports and entry points (tree-sitter, regex fallback). |
| `scan_sast` | Static security analysis via Semgrep (with a built-in heuristic fallback). |
| `scan_dependencies` | Software Composition Analysis via the free OSV.dev API. |
| `scan_secrets` | Detect hardcoded secrets; values are always redacted. |
| `assess_quality` | Maintainability heuristics + a 0-100 health score. |
| `explain_codebase` | LLM explanation of architecture and flows. |
| `recommend_improvements` | LLM-prioritized, actionable recommendations. |
| `generate_report` | Assemble a final Markdown report. |

Orchestration flow:

```
ingest_repository
  -> analyze_structure
  -> [scan_sast, scan_dependencies, scan_secrets, assess_quality]
  -> explain_codebase
  -> recommend_improvements
  -> generate_report
```

## Requirements

- Python 3.11+
- AWS credentials with Amazon Bedrock access (for agent mode)
- Optional external scanners for full coverage:
  - [Semgrep](https://semgrep.dev/docs/getting-started/) for SAST. Install via
    the extra: `pip install -e ".[scanners]"` (or `pip install semgrep`).
    `scan_sast` detects it automatically; without it, built-in heuristics run.
  - [Trivy](https://aquasecurity.github.io/trivy/) (optional, alternative SCA/secret scanning)

## Installation

```bash
# From the project root
python3 -m venv .venv
source .venv/bin/activate

pip install -e .
# For development (tests):
pip install -e ".[dev]"
```

## Configuration

Copy `.env.example` and adjust as needed, or export the variables directly:

```bash
export AWS_REGION=us-east-2                 # project region
export AWS_PROFILE=my-Free-tier             # profile created via `aws login`
export BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0
```

This project targets the **new AWS experience** with the project in
**us-east-2**. The cross-region US inference profile
`us.amazon.nova-pro-v1:0` is used so Nova Pro is invocable from us-east-2.

Enable model access for Amazon Nova Pro in the Bedrock console before running
in agent mode.

## Usage

Agent mode (LLM-driven, needs Bedrock):

```bash
code-copilot https://github.com/octocat/Hello-World
# or a local path
code-copilot /path/to/repo --report reports/hello.md
```

Offline mode (deterministic static pipeline, no AWS needed):

```bash
code-copilot /path/to/repo --offline --report reports/hello.md
```

You can also run without installing the console script:

```bash
python -m code_copilot.cli /path/to/repo --offline
```

### Colored output

- Agent responses: bright blue, prefixed with `agent>`.
- Each tool: a distinct, stable color, shown as a bracketed label like
  `[scan_sast] running...`.
- Colors auto-disable when output is not a TTY or when `NO_COLOR` is set.

## MCP servers (optional integrations)

The prompt targets these external integrations. They are optional; the tools
degrade gracefully without them.

- **GitHub MCP server** — richer repo/PR/code-scanning access. Configure it in
  your AI tool's MCP settings and set `GITHUB_TOKEN`.
- **Semgrep** — install the CLI (`pip install semgrep`) or run the Semgrep MCP
  server; `scan_sast` uses the CLI automatically when present.
- **Trivy CLI** — optional alternative scanner for SCA/secrets/containers.
- **OSV.dev API** — used by `scan_dependencies` over HTTPS; no auth required.

## Security model

- Repository content is treated as untrusted. The agent **never executes** repo
  code; it only reads and analyzes statically.
- Secret values are never printed or logged. Findings reference only the type,
  location, and a redacted preview.
- If a scanner is missing or fails, the pipeline continues and reports the
  limitation instead of aborting.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Project layout

```
src/code_copilot/
  cli.py          # terminal entry point (argparse)
  agent.py        # Strands agent wiring
  callback.py     # colored streaming callback handler
  colors.py       # ANSI color helpers
  model.py        # Bedrock Nova Pro model factory
  pipeline.py     # offline deterministic pipeline
  utils.py        # shared helpers (walking, detection, redaction)
  tools/          # the nine @tool implementations
tests/            # unit tests per tool + end-to-end pipeline test
```
