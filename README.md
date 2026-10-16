# Antigravity Conversation Migrator

A zero-dependency, bidirectional conversation history, session, and workspace migrator between **Google Antigravity IDE (1.x)** and **Antigravity 2.0**.

[![Version](https://img.shields.io/badge/version-0.7.0-blue.svg)](#)
[![Python](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)

## Table of Contents

- [Quick Start](#quick-start)
- [Overview & Architecture Shift](#overview--architecture-shift)
- [Features](#features)
- [Architecture](#architecture)
- [CLI Reference](#cli-reference)
- [Safety & Backups](#safety--backups)
- [Testing](#testing)
- [License](#license)

## Quick Start

The recommended way to run the migrator is in interactive mode. It automatically detects your Antigravity installations, checks for active processes, displays your conversation statistics, and guides you step-by-step.

### Prerequisites

- Python 3.8 or higher
- Supported platforms: Windows, macOS, Linux / WSL

### Launch Interactive Mode

```bash
git clone https://github.com/NightMean/Antigravity-Conversation-Migrator.git
cd Antigravity-Conversation-Migrator
python migrator.py
```

When launched without arguments, the interactive assistant will:
1. Scan standard installation locations for Antigravity IDE (1.x) and Antigravity 2.0.
2. Check for running Antigravity processes to ensure data is not overwritten by Electron in-memory caches.
3. Display discovered session and conversation counts for each environment.
4. Prompt you to select your desired migration direction (`ide-to-2`, `2-to-ide`, `both`), run a simulation preview (`dry-run`), or specify custom paths.

## Overview & Architecture Shift

Antigravity 2.0 introduces an entirely redesigned storage architecture and project management model. Because the two versions operate with different file schemas and directory structures, conversations and workspaces from Antigravity IDE (1.x) are not automatically migrated upon upgrading.

### Architecture Comparison

| Aspect | Antigravity IDE (1.x) | Antigravity 2.0 | Migrator Solution |
| :--- | :--- | :--- | :--- |
| **Conversation Index** | Base64 Protobuf map in `state.vscdb` (`trajectorySummaries`) | SQLite database (`conversation_summaries.db`) and binary proto (`agyhub_summaries_proto.pb`) | Bidirectional decoder and encoder converting between both formats |
| **Workspace Model** | Folder URI keyed map | First-Class Projects with dedicated `project_id` UUIDs | Deterministic `UUIDv5` generation and Protobuf Field 18 binding |
| **Project Registry** | Implicit folder associations | Explicit `projectsOrder` list in `app_storage.json` | Automatic registration in 2.0 projects list |
| **Session Storage** | `~/.gemini/antigravity-ide/conversations/` and `brain/` | `~/.gemini/antigravity/conversations/` and `brain/` | Automatic file replication and synchronization |
| **Process Cache** | In-memory Electron SQLite cache | In-memory Electron SQLite cache | Pre-flight process guard and timestamped file backups |

## Features

### Bidirectional Migration & Synchronization
- **IDE to 2.0 (`ide-to-2`)**: Imports legacy IDE conversation sessions into 2.0, unpacks Protobuf records into SQLite rows, updates the binary search index, and registers workspaces.
- **2.0 to IDE (`2-to-ide`)**: Backports conversations and sessions created in Antigravity 2.0 into the IDE's `state.vscdb` Protobuf schema, enabling complete portability back to 1.x.
- **Two-Way Synchronization (`both`)**: Keeps both environments in sync without creating duplicate conversation entries.

### First-Class Project Promotion
- Identifies unassigned workspace folders and elevates them into first-class Project entities in the Antigravity 2.0 sidebar.
- Generates stable, deterministic `UUIDv5` identifiers from folder paths, ensuring repeated migrations never duplicate projects.
- Injects the `project_id` into Protobuf Field 18 of conversation summary blobs and updates `projectsOrder` in `app_storage.json`.

### Intelligent Drive & Path Normalization
- Automatically recognizes relocated projects across drive letters (for example, repositories moved from `J:\Projects\...` to `F:\Projects\...`).
- Matches folder structures and merges conversations into existing projects rather than generating phantom project entries.
- Normalizes URL-encoded file paths transparently across operating systems (`file:///C:/...` vs `file:///c%3A/...`).

### Built-in Pure-Python Protobuf Engine
- Operates entirely with zero third-party dependencies using Python's standard library (`sqlite3`, `json`, `urllib`, `hashlib`, `uuid`, `pathlib`).
- Features a purpose-built pure-Python Protobuf wire format decoder and encoder, eliminating the need for `protoc`, compiled proto stubs, or native extensions.

### Safe by Design
- **Process Guard**: Automatically detects running instances of `Antigravity.exe`, `antigravity-manager.exe`, and `Antigravity IDE.exe` to prevent database overwrites on application shutdown.
- **Automated Backups**: Creates timestamped snapshot backups of every database and JSON state file before performing any modifications.
- **Safe Dry Run**: Supports full simulation via `--dry-run`, detailing all file copies, project creations, and database writes without modifying any files.

## Architecture

```
                                  MIGRATION BRIDGE
                                  
  Antigravity IDE (1.x)                               Antigravity 2.0
┌───────────────────────────────┐                   ┌───────────────────────────────────┐
│ ~/.gemini/antigravity-ide/    │                   │ ~/.gemini/antigravity/            │
│  ├── conversations/*.db       │ ◄───────────────► │  ├── conversations/*.db           │
│  └── brain/<UUID>/            │    File Copy      │  └── brain/<UUID>/                │
│                               │                   │                                   │
│ AppData/.../Antigravity IDE/  │                   │ ~/.gemini/antigravity/            │
│  └── User/globalStorage/      │                   │  ├── conversation_summaries.db    │
│       └── state.vscdb         │ ◄───────────────► │  └── agyhub_summaries_proto.pb    │
│            (Protobuf Base64)  │   Schema Adapt    │                                   │
│                               │                   │ AppData/.../Antigravity/          │
│                               │                   │  ├── app_storage.json             │
│                               │                   │  │    (projectsOrder registry)    │
│                               │                   │  └── User/globalStorage/          │
│                               │                   │       └── state.vscdb             │
└───────────────────────────────┘                   └───────────────────────────────────┘
```

## CLI Reference

The command-line interface provides complete headless control for automated workflows, scripting, or environments with non-standard paths.

### Common Commands

| Task | Command |
| :--- | :--- |
| **Inspect Environment Status** | `python migrator.py --status` |
| **Simulate Changes (Dry Run)** | `python migrator.py --dry-run` |
| **Migrate IDE to 2.0 (Default)** | `python migrator.py --direction ide-to-2` |
| **Migrate 2.0 back to IDE** | `python migrator.py --direction 2-to-ide` |
| **Bidirectional Sync** | `python migrator.py --direction both` |
| **Custom AppData / Paths** | `python migrator.py --ide-path "<path>" --ag2-path "<path>" --gemini-dir "<path>"` |
| **Bypass Process Lock Check** | `python migrator.py --force` |

### CLI Options

```
usage: antigravity-migrator [-h] [--direction {ide-to-2,2-to-ide,both}]
                            [--dry-run] [--status] [--force]
                            [--non-interactive] [--ide-path IDE_PATH]
                            [--ag2-path AG2_PATH] [--gemini-dir GEMINI_DIR]
                            [-v]

Bidirectional conversation history & project migrator between Antigravity IDE
and Antigravity 2.0.

options:
  -h, --help            show this help message and exit
  --direction {ide-to-2,2-to-ide,both}
                        Direction of migration (ide-to-2, 2-to-ide, both)
  --dry-run             Preview all planned changes without modifying any
                        files or databases
  --status              Display current conversation and project counts across
                        both environments
  --force               Skip active process detection check (Caution: Electron
                        can overwrite databases on exit)
  --non-interactive     Run without interactive prompts (uses default
                        --direction ide-to-2 unless specified)
  --ide-path IDE_PATH   Custom path to Antigravity IDE AppData directory
  --ag2-path AG2_PATH   Custom path to Antigravity 2.0 AppData directory
  --gemini-dir GEMINI_DIR
                        Custom path to .gemini directory
  -v, --version         Show version number and exit
```

## Safety & Backups

Before writing any changes, the tool generates timestamped backups in the respective storage directories:

| Target File | Backup Pattern | Purpose |
| :--- | :--- | :--- |
| `state.vscdb` | `state.vscdb.backup_YYYYMMDD_HHMMSS` | Preserves IDE trajectory Protobuf state |
| `conversation_summaries.db` | `conversation_summaries.db.backup_YYYYMMDD_HHMMSS` | Preserves 2.0 SQLite conversation index |
| `agyhub_summaries_proto.pb` | `agyhub_summaries_proto.pb.backup_YYYYMMDD_HHMMSS` | Preserves 2.0 binary Protobuf search index |
| `app_storage.json` | `app_storage.json.backup_YYYYMMDD_HHMMSS` | Preserves 2.0 workspace and project list |

To revert any migration run, close Antigravity and restore the backup files by removing the `.backup_timestamp` suffix.

## Testing

The test suite runs using Python's built-in `unittest` framework and tests Protobuf encoding, path detection, and schema adaptation against isolated mock databases:

```bash
python -m unittest discover -s tests
```

## License

This project is licensed under the [MIT License](LICENSE).
