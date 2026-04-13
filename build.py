#!/usr/bin/env python3
"""Build loop.py from loop.py.tpl by inlining prompt markdown files.

Usage:
    python build.py
"""

import sys
from pathlib import Path

TEMPLATE = "loop.py.tpl"
OUTPUT = "loop.py"

# Map placeholder tags to their source markdown files
PLACEHOLDERS = {
    "{{BUILD_PROMPT}}": "PROMPT_build.md",
    "{{PLAN_PROMPT}}": "PROMPT_plan.md",
    "{{SPEC_PROMPT}}": "PROMPT_spec.md",
}


def main() -> int:
    tpl_path = Path(TEMPLATE)
    if not tpl_path.exists():
        print(f"Error: template not found: {TEMPLATE}")
        return 1

    template = tpl_path.read_text()

    for placeholder, md_file in PLACEHOLDERS.items():
        md_path = Path(md_file)
        if not md_path.exists():
            print(f"Error: prompt file not found: {md_file}")
            return 1
        content = md_path.read_text().rstrip("\n")
        template = template.replace(placeholder, content)

    Path(OUTPUT).write_text(template)
    print(f"Built {OUTPUT} from {TEMPLATE} + {', '.join(PLACEHOLDERS.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
