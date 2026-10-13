"""Keep maintained Markdown links and package metadata consistent."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]+\")?\)")


def _documents():
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "demo").rglob("*.md"))
    return files


def test_local_markdown_links_exist_and_code_fences_are_balanced():
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        assert text.count("```") % 2 == 0, str(document)
        for link in LINK_PATTERN.findall(text):
            target = link.split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            assert (document.parent / target).is_file(), (
                "{} links to missing file {}".format(document, link)
            )


def test_python_distribution_uses_maintained_readme():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'readme = "README.md"' in metadata
