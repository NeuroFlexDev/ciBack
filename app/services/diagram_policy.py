"""Generation contract: bounded native Mermaid, never remote or executable diagrams."""
import re

FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(mermaid|uml|plantuml|diagram)[ \t]*\n(.*?)^[ \t]*\1[ \t]*$", re.M | re.S | re.I)
FORBIDDEN = re.compile(r"%%\s*\{|^\s*---|<\/?[a-z!]|\b(?:https?|javascript|data|file|ftp):|@|\\|url\s*\(|(?:^|[;\n])\s*(?:click|link|links|callback|style|classDef|linkStyle|cssClass|icon|image|!include)\b", re.M | re.I)
HEADER = re.compile(r"^(?:flowchart\s+(?:TB|TD|BT|LR|RL)|graph\s+(?:TB|TD|BT|LR|RL)|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|pie(?:\s+showData)?|journey|timeline|mindmap|quadrantChart|xychart(?:-beta)?)\b")


def validate_generated_diagrams(markdown: str) -> str:
    """Reject unsafe generation, retain safe source exactly. Browser validates full syntax."""
    for match in FENCE.finditer(markdown):
        source = match[3].strip()
        if len(source) > 12000 or len(source.splitlines()) > 160 or len(re.findall(r"--|==|\.\.|->|<-", source)) > 120:
            raise ValueError("Diagram exceeds 12000 characters, 160 lines or 120 connections")
        header = next((line.strip() for line in source.splitlines() if line.strip() and not line.strip().startswith("%%")), "")
        if FORBIDDEN.search(source) or not HEADER.match(header):
            raise ValueError("Use native Mermaid flow/class/state/sequence/ER diagrams without HTML, links, styles, configuration directives or external services")
    return markdown
