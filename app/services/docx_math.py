"""Preserve common Office Math structures as LaTeX instead of dropping equations."""

from lxml.etree import QName


def _nary_latex(node, part):
    operator = "∫"
    for child in node.iter():
        if QName(child).localname == "chr":
            operator = next(iter(child.attrib.values()), operator)
    operator = {"∫": r"\int", "∑": r"\sum", "∏": r"\prod"}.get(operator, operator)
    return (
        operator
        + ("_{" + part("sub") + "}" if part("sub") else "")
        + ("^{" + part("sup") + "}" if part("sup") else "")
        + " "
        + part("e")
    )


def _fraction_or_script(tag, part):
    if tag == "f":
        return r"\frac{" + part("num") + "}{" + part("den") + "}"
    if tag == "sSup":
        return "{" + part("e") + "}^{" + part("sup") + "}"
    if tag == "sSub":
        return "{" + part("e") + "}_{" + part("sub") + "}"
    return "{" + part("e") + "}_{" + part("sub") + "}^{" + part("sup") + "}"


def _latex(node):
    tag = QName(node).localname
    children = {QName(child).localname: child for child in node}

    def part(name):
        return _latex(children[name]) if name in children else ""

    if tag == "t":
        return node.text or ""
    if tag.endswith("Pr"):
        return ""
    if tag in {"f", "sSup", "sSub", "sSubSup"}:
        return _fraction_or_script(tag, part)
    if tag == "rad":
        degree = part("deg")
        return r"\sqrt" + ("[" + degree + "]" if degree else "") + "{" + part("e") + "}"
    if tag == "m":
        return (
            r"\begin{matrix}"
            + r" \\ ".join(_latex(child) for child in node if QName(child).localname == "mr")
            + r"\end{matrix}"
        )
    if tag == "mr":
        return " & ".join(_latex(child) for child in node if QName(child).localname == "e")
    if tag == "nary":
        return _nary_latex(node, part)
    return "".join(_latex(child) for child in node)


def paragraph_with_math(paragraph):
    def visit(node):
        tag = QName(node).localname
        if tag == "oMath":
            return "$" + _latex(node) + "$"
        if tag == "t":
            return node.text or ""
        if tag in {"tab", "br", "cr"}:
            return " "
        return "".join(visit(child) for child in node)

    return visit(paragraph._p)
