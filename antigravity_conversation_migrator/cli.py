"""
Command Line & Interactive Terminal Interface for Antigravity Conversation Migrator
===================================================================================
Provides both an intuitive interactive menu (when launched without arguments) and
a comprehensive headless CLI for scriptable automation.
"""

import sys
import argparse
from pathlib import Path
from typing import Optional

from . import __version__
from .paths import AntigravityPaths, check_active_processes
from .migrator import Migrator

def format_table(rows: list, headers: list) -> str:
    """Formats a clean CLI table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    
    header_str = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_str = "  ".join("-" * widths[i] for i in range(len(headers)))
    row_strs = ["  ".join(str(val).ljust(widths[i]) for i, val in enumerate(row)) for row in rows]
    return f"{header_str}\n{sep_str}\n" + "\n".join(row_strs)

def print_banner(dry_run: bool = False, direction: Optional[str] = None, chats_only: bool = False):
    print("=" * 70)
    tag = " [DRY RUN]" if dry_run else ""
    print(f"  Antigravity Conversation Migrator v{__version__}{tag}")
    if direction:
        mode_str = " (Chats Only - No Projects)" if chats_only else ""
        print(f"  Direction: {direction}{mode_str}")
    print("=" * 70)

def run_migration_flow(paths: AntigravityPaths, direction: str, dry_run: bool, force: bool, chats_only: bool = False):
    print_banner(dry_run=dry_run, direction=direction, chats_only=chats_only)

    # 1. Process Guard
    print("\n[1/4] Checking running processes...")
    if not dry_run and not force:
        running = check_active_processes()
        if running:
            print(f"[-] ERROR: Active processes detected: {', '.join(running)}")
            print("    Please completely close Antigravity and Antigravity Manager before migrating.")
            print("    (Electron keeps SQLite state cached in-memory and overwrites on close).")
            print("    Use --force only if you know what you are doing.")
            return False
        print("      No conflicting processes detected.")
    else:
        print("      Process check bypassed.")

    # 2. Database & State Backup
    print("\n[2/4] Safety Backups:")
    backed_up = paths.create_backups(dry_run=dry_run)
    for b in backed_up:
        prefix = "[DRY RUN] Would backup:" if dry_run else "Backed up:"
        print(f"      {prefix} {b.name}")

    # 3. Perform Migration
    print("\n[3/4] Migrating conversations and projects...")
    migrator = Migrator(paths=paths)

    if direction in ("ide-to-2", "both"):
        res = migrator.migrate_ide_to_2(dry_run=dry_run, chats_only=chats_only)
        print("\n  --> IDE -> 2.0 Results:")
        print(f"      New raw conversation files copied: {res['copied_conv_files']}")
        print(f"      Brain session transcripts copied:  {res['copied_brain_dirs']}")
        print(f"      WorkspaceStorage caches copied:    {res['copied_workspace_storage']}")
        if not chats_only:
            print(f"      New 2.0 projects promoted:         {res['new_projects_promoted']}")
            print(f"      Conversations assigned to projects:{res['conversations_assigned']}")
            print(f"      Total sidebar projects in 2.0:     {res['total_sidebar_projects']}")
        else:
            print("      Project entity promotion:          Skipped (--chats-only)")

    if direction in ("2-to-ide", "both"):
        res = migrator.migrate_2_to_ide(dry_run=dry_run, chats_only=chats_only)
        print("\n  --> 2.0 -> IDE Results:")
        print(f"      New raw conversation files copied: {res['copied_conv_files']}")
        print(f"      Brain session transcripts copied:  {res['copied_brain_dirs']}")
        print(f"      WorkspaceStorage caches copied:    {res['copied_workspace_storage']}")
        print(f"      New conversations indexed in IDE:  {res['indexed_in_ide']}")

    # 4. Completion
    print("\n[4/4] Summary:")
    if dry_run:
        print("[+] DRY RUN COMPLETED: All operations simulated without errors.")
        print(f"    Ready to apply live: run with --direction {direction}")
    else:
        print("[+] MIGRATION COMPLETED SUCCESSFULLY!")
        print("    You can now open Antigravity to see all your migrated projects and chats.")
    return True

def print_status_report(paths: AntigravityPaths):
    migrator = Migrator(paths=paths)
    report = migrator.get_status_report()
    summary = paths.get_detection_summary()

    print("\n" + "=" * 65)
    print(f"  Antigravity Installation & Status Report (v{__version__})")
    print("=" * 65)

    print("\nDetected Locations:")
    ide_status = "[Found]" if summary["ide"]["detected"] else "[Not Found]"
    ag2_status = "[Found]" if summary["ag2"]["detected"] else "[Not Found]"
    print(f"  * Antigravity IDE (1.x): {ide_status}")
    print(f"    - AppData:       {summary['ide']['appdata']}")
    print(f"    - state.vscdb:   {summary['ide']['state_vscdb']}")
    print(f"    - Conversations: {summary['ide']['conversations_dir']}")
    print(f"  * Antigravity 2.0:       {ag2_status}")
    print(f"    - AppData:       {summary['ag2']['appdata']}")
    print(f"    - Database:      {summary['ag2']['conversation_summaries']}")
    print(f"    - Conversations: {summary['ag2']['conversations_dir']}")

    print("\nConversation & Project Metrics:")
    rows = [
        ["IDE Conversation Files (.db)", str(report["ide_conv_files"])],
        ["IDE Indexed in state.vscdb", str(report["ide_indexed_convs"])],
        ["2.0 Conversation Files (.db)", str(report["ag2_conv_files"])],
        ["2.0 Total Indexed in SQLite", str(report["ag2_indexed_convs"])],
        ["2.0 Active Project Entities", str(report["ag2_projects_count"])],
        ["2.0 Unassigned / Loose Chats", str(report["ag2_unassigned_convs"])],
    ]
    print(format_table(rows, ["Metric", "Value"]))

def run_interactive_menu():
    """Interactive text UI launched when user executes script without arguments."""
    paths = AntigravityPaths()

    while True:
        print("\n" + "=" * 70)
        print(f"        Antigravity Conversation Migrator v{__version__}")
        print("=" * 70)

        summary = paths.get_detection_summary()
        ide_tag = "[+ Detected]" if summary["ide"]["detected"] else "[- Not Detected]"
        ag2_tag = "[+ Detected]" if summary["ag2"]["detected"] else "[- Not Detected]"

        print(f"\nEnvironment Detection:")
        print(f"  Antigravity IDE (1.x): {ide_tag} -> {paths.ide_appdata}")
        print(f"  Antigravity 2.0:       {ag2_tag} -> {paths.ag2_appdata}")

        print("\nChoose an action:")
        print("  [1] Migrate Antigravity IDE (1.x) -> Antigravity 2.0 (Full: Chats + Projects)")
        print("  [2] Migrate Antigravity IDE (1.x) -> Antigravity 2.0 (Chats Only - No Projects)")
        print("  [3] Migrate Antigravity 2.0 -> Antigravity IDE (1.x)")
        print("  [4] Bidirectional Full Sync (Both directions)")
        print("  [5] Inspect Status & Conversation Counts")
        print("  [6] Run Dry-Run Simulation (Safe preview without changes)")
        print("  [7] Configure Custom Paths / Overrides")
        print("  [0] Exit")

        try:
            choice = input("\nEnter choice [0-7] (default: 1): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not choice:
            choice = "1"

        if choice == "0":
            print("Exiting.")
            break
        elif choice == "1":
            confirm = input("Run as dry-run preview first? [Y/n]: ").strip().lower()
            dry_run = confirm != "n"
            run_migration_flow(paths, direction="ide-to-2", dry_run=dry_run, force=False, chats_only=False)
            if dry_run:
                run_live = input("\nWould you like to execute the actual migration now? [y/N]: ").strip().lower()
                if run_live == "y":
                    run_migration_flow(paths, direction="ide-to-2", dry_run=False, force=False, chats_only=False)
        elif choice == "2":
            confirm = input("Run as dry-run preview first? [Y/n]: ").strip().lower()
            dry_run = confirm != "n"
            run_migration_flow(paths, direction="ide-to-2", dry_run=dry_run, force=False, chats_only=True)
            if dry_run:
                run_live = input("\nWould you like to execute the actual migration now? [y/N]: ").strip().lower()
                if run_live == "y":
                    run_migration_flow(paths, direction="ide-to-2", dry_run=False, force=False, chats_only=True)
        elif choice == "3":
            confirm = input("Run as dry-run preview first? [Y/n]: ").strip().lower()
            dry_run = confirm != "n"
            run_migration_flow(paths, direction="2-to-ide", dry_run=dry_run, force=False)
            if dry_run:
                run_live = input("\nWould you like to execute the actual migration now? [y/N]: ").strip().lower()
                if run_live == "y":
                    run_migration_flow(paths, direction="2-to-ide", dry_run=False, force=False)
        elif choice == "4":
            confirm = input("Run as dry-run preview first? [Y/n]: ").strip().lower()
            dry_run = confirm != "n"
            run_migration_flow(paths, direction="both", dry_run=dry_run, force=False)
            if dry_run:
                run_live = input("\nWould you like to execute the actual migration now? [y/N]: ").strip().lower()
                if run_live == "y":
                    run_migration_flow(paths, direction="both", dry_run=False, force=False)
        elif choice == "5":
            print_status_report(paths)
        elif choice == "6":
            run_migration_flow(paths, direction="ide-to-2", dry_run=True, force=False)
        elif choice == "7":
            print("\nConfigure Custom Paths (press Enter to keep current):")
            new_ide = input(f"Antigravity IDE path [{paths.ide_appdata}]: ").strip()
            new_ag2 = input(f"Antigravity 2.0 path [{paths.ag2_appdata}]: ").strip()
            new_gem = input(f".gemini directory [{paths.gemini}]: ").strip()
            paths = AntigravityPaths(
                custom_ide_path=new_ide or str(paths.ide_appdata),
                custom_ag2_path=new_ag2 or str(paths.ag2_appdata),
                custom_gemini_dir=new_gem or str(paths.gemini)
            )
            print("Paths updated successfully.")
        else:
            print("Invalid choice, please select 0-7.")

        input("\nPress Enter to return to menu...")

def main():
    parser = argparse.ArgumentParser(
        prog="antigravity-migrator",
        description="Bidirectional conversation history & project migrator between Antigravity IDE and Antigravity 2.0."
    )
    parser.add_argument(
        "--direction",
        choices=["ide-to-2", "2-to-ide", "both"],
        default=None,
        help="Direction of migration (ide-to-2, 2-to-ide, both)"
    )
    parser.add_argument(
        "--chats-only", "--skip-projects", "--no-projects",
        action="store_true",
        dest="chats_only",
        help="Migrate conversation sessions and history only, without promoting workspaces to First-Class Projects in 2.0"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all planned changes without modifying any files or databases"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display current conversation and project counts across both environments"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip active process detection check (Caution: Electron can overwrite databases on exit)"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without interactive prompts (uses default --direction ide-to-2 unless specified)"
    )
    parser.add_argument(
        "--ide-path",
        type=str,
        default=None,
        help="Custom path to Antigravity IDE AppData directory"
    )
    parser.add_argument(
        "--ag2-path",
        type=str,
        default=None,
        help="Custom path to Antigravity 2.0 AppData directory"
    )
    parser.add_argument(
        "--gemini-dir",
        type=str,
        default=None,
        help="Custom path to .gemini directory"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version number and exit"
    )

    args = parser.parse_args()

    # If no flags are provided and user launched interactively from terminal:
    if len(sys.argv) == 1 and not args.non_interactive:
        run_interactive_menu()
        return

    # CLI mode
    paths = AntigravityPaths(
        custom_ide_path=args.ide_path,
        custom_ag2_path=args.ag2_path,
        custom_gemini_dir=args.gemini_dir,
    )

    if args.status:
        print_status_report(paths)
        return

    direction = args.direction or "ide-to-2"
    run_migration_flow(paths, direction=direction, dry_run=args.dry_run, force=args.force, chats_only=args.chats_only)

if __name__ == "__main__":
    main()
