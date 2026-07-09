#!/usr/bin/env python3
"""Create a lightweight spec artifact under ai/specs/."""

import argparse
import datetime as _dt
import os
import re
import sys


def slugify(value):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "spec"


def read_template(repo_root):
    candidates = [
        os.path.join(repo_root, "ai", "spec-template.md"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "spec-template.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
    return "# Spec\n\n## Title\n\n<!-- Short feature, bugfix, or change name. -->\n"


def create_spec(repo_root, title, date, overwrite=False):
    specs_dir = os.path.join(repo_root, "ai", "specs")
    os.makedirs(specs_dir, exist_ok=True)
    filename = "{}--{}.md".format(date, slugify(title))
    path = os.path.join(specs_dir, filename)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    template = read_template(repo_root)
    content = template.replace(
        "<!-- Short feature, bugfix, or change name. -->",
        title,
        1,
    )
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content.rstrip() + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title", help="Spec title")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--date", default=None, help="Date prefix YYYY-MM-DD (default: today)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing spec with the same slug")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo)
    date = args.date or _dt.date.today().isoformat()
    try:
        path = create_spec(repo_root, args.title, date, overwrite=args.overwrite)
    except FileExistsError as exc:
        print("spec already exists: {}".format(exc), file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
