#!/usr/bin/env python3
"""Synchronize existing top-level workspace/global copies of this skill package."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without changing files")
    parser.add_argument("--workspace", type=Path, required=True, help="Directory containing service repositories")
    parser.add_argument("--global-skills", type=Path, default=Path.home() / ".codex/skills")
    args = parser.parse_args()
    source = None
    for repository in ("i-migration-service", "migration-service"):
        candidate = args.workspace / repository / ".agents/skills/database-migrations"
        if (candidate / "SKILL.md").is_file():
            source = candidate.resolve()
            break
    if source is None:
        parser.error("canonical database-migrations package not found in workspace i-migration-service or migration-service")
    roots = {p.parent.resolve() for p in args.workspace.glob("*/.agents/skills/database-migrations/SKILL.md")}
    global_copy = args.global_skills / "database-migrations"
    if (global_copy / "SKILL.md").exists():
        roots.add(global_copy.resolve())
    roots.discard(source)
    files = [p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    drift = []
    for root in sorted(roots):
        for item in files:
            target = root / item.relative_to(source)
            if target.exists() and target.read_bytes() == item.read_bytes():
                continue
            drift.append(str(target))
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
    print(f"{len(roots)} copies checked; {len(drift)} files {'differ' if args.check else 'updated'}")
    if args.check:
        for path in drift:
            print(path)
    return int(args.check and bool(drift))


if __name__ == "__main__":
    raise SystemExit(main())
