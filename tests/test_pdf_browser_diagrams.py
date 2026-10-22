"""Regression fixtures downloaded through the real editor, not JSDOM geometry."""
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from defusedxml import ElementTree as ET
from PIL import Image, ImageChops
import pymupdf
import pytest
from reportlab.graphics import renderPDF
from reportlab.platypus import Spacer

from app.services.pdf_assets import drawing_from_svg, PDFRenderError, safe_svg
from app.services.pdf_document import ContentBuilder, CourseDocument, fonts

FIXTURES = Path(__file__).parent / 'fixtures/pdf'


def pdf_drawing(source):
    fonts()
    drawing = drawing_from_svg(source, 10000, 10000, normalize=True)
    return pymupdf.open(stream=renderPDF.drawToString(drawing), filetype='pdf')


def pixels(page):
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    return Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)


@pytest.mark.parametrize('name,expected_count', [('mermaid-browser.svg', 5), ('sequence-browser.svg', 4)])
def test_actual_browser_marker_arrowheads_render_at_each_edge_endpoint(name, expected_count, caplog):
    source = (FIXTURES / name).read_text(encoding='utf8')
    root = ET.fromstring(source)
    left, top, width, height = map(float, root.attrib['viewBox'].split())
    endpoints = []
    for node in root.iter():
        marker = node.attrib.pop('marker-end', None)
        if marker and marker != 'none':
            if node.tag.endswith('line'):
                endpoints.append((float(node.attrib['x2']), float(node.attrib['y2'])))
            else:
                values = re.findall(r'-?\d+(?:\.\d+)?', node.attrib['d'])
                endpoints.append(tuple(map(float, values[-2:])))
    assert len(endpoints) == expected_count
    without_markers = ET.tostring(root, encoding='unicode')
    original = pdf_drawing(source)
    stripped = pdf_drawing(without_markers)
    original_pixels, stripped_pixels = pixels(original[0]), pixels(stripped[0])
    difference = ImageChops.difference(original_pixels, stripped_pixels).convert('L')
    # Old svglib ignored marker-end entirely: images were identical. Checking
    # the endpoint neighborhoods also catches arrows drawn in the wrong place.
    for x, y in endpoints:
        px, py = (x-left)*original_pixels.width/width, (y-top)*original_pixels.height/height
        crop = difference.crop((int(px-28), int(py-28), int(px+28), int(py+28)))
        assert sum(crop.histogram()[9:]) > 20
    assert len(original[0].get_images()) == 0  # Remains crisp vector geometry.
    assert not any('Unable to find a clipping path' in record.message for record in caplog.records)


def test_actual_browser_nested_em_tspans_remain_inside_their_node_on_two_lines():
    document = pdf_drawing((FIXTURES / 'mermaid-browser.svg').read_text(encoding='utf8'))
    # The downloaded SVG's first node is at (8,131.65)..(188,196.25).
    # The two light-filled paths inside it are the shaped label lines. Previously the
    # nested spans collapsed onto the rectangle's top border and overlapped.
    glyphs = [path['rect'] for path in document[0].get_drawings()
        if path.get('fill') and max(abs(value-.8) for value in path['fill']) < .02
        and 40 < path['rect'].width < 150 and path['rect'].height < 22
        and 8 <= path['rect'].x0 <= 188 and 131 <= path['rect'].y0 <= 197]
    assert len(glyphs) == 2
    assert all(20 < glyph.x0 < glyph.x1 < 176 and 140 < glyph.y0 < glyph.y1 < 188 for glyph in glyphs)
    upper, lower = sorted(glyphs, key=lambda rectangle: rectangle.y0)
    assert lower.y0 - upper.y1 > 4
    assert all(abs((glyph.x0 + glyph.x1)/2 - 98) < 4 for glyph in glyphs)


def test_actual_browser_sequence_arrows_point_to_the_receiver_in_both_directions():
    document = pdf_drawing((FIXTURES / 'sequence-browser.svg').read_text(encoding='utf8'))
    # SVG viewBox starts at (-50,-10), hence the +50/+10 PDF page coordinates.
    for endpoint_x, endpoint_y, direction in [(278, 109, 1), (79, 153, -1), (278, 197, 1), (79, 241, -1)]:
        triangles = [path for path in document[0].get_drawings() if path.get('fill')
            and 5 < path['rect'].width < 15 and 5 < path['rect'].height < 15
            and abs((path['rect'].y0 + path['rect'].y1)/2 - endpoint_y - 10) < 1]
        assert len(triangles) == 1
        points = {tuple(point) for item in triangles[0]['items'] for point in item[1:] if isinstance(point, pymupdf.Point)}
        tip = [point for point in points if abs(point[1] - endpoint_y - 10) < 1]
        assert len(tip) == 1
        base = [point for point in points if point not in tip]
        assert all((tip[0][0] - point[0]) * direction > 5 for point in base)
        assert abs(tip[0][0] - endpoint_x - 50) < 4


@pytest.mark.parametrize('style', [False, True])
@pytest.mark.parametrize('viewbox', ['', 'viewBox="-2 -3 20 14" preserveAspectRatio="xMinYMax meet"'])
def test_auto_start_reverse_matches_explicit_rotations_with_start_and_end_markers(style, viewbox):
    marker = f'<marker id="arrow" refX="7.9" refY="5" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" {viewbox} orient="auto-start-reverse"><path d="M -1 0 L 10 5 L 0 10 Z" fill="#17191d"/></marker>'
    bindings = 'style="marker:url(#arrow)"' if style else 'marker-start="url(#arrow)" marker-end="url(#arrow)"'
    source = f'<svg xmlns="http://www.w3.org/2000/svg" width="250" height="100"><defs>{marker}</defs><line x1="30" y1="25" x2="220" y2="25" stroke="black" {bindings}/><line x1="220" y1="75" x2="30" y2="75" stroke="black" {bindings}/></svg>'
    forward_marker = marker.replace('auto-start-reverse', '0')
    back_marker = marker.replace('id="arrow"', 'id="back"').replace('auto-start-reverse', '180')
    expected = f'<svg xmlns="http://www.w3.org/2000/svg" width="250" height="100"><defs>{forward_marker}{back_marker}</defs><line x1="30" y1="25" x2="220" y2="25" stroke="black" marker-start="url(#back)" marker-end="url(#arrow)"/><line x1="220" y1="75" x2="30" y2="75" stroke="black" marker-start="url(#arrow)" marker-end="url(#back)"/></svg>'
    actual_document, expected_document = pdf_drawing(source), pdf_drawing(expected)
    difference = ImageChops.difference(pixels(actual_document[0]), pixels(expected_document[0])).convert('L')
    assert sum(difference.histogram()[20:]) < 10


def test_heading_stays_with_following_formula_when_page_has_only_heading_space():
    fonts()
    content = {'course': {'title': 'Pagination'}, 'modules': [{'title': 'Module', 'lessons': [
        {'title': 'Lesson', 'content': '### Formula heading\n\n$$\\bar{x}=\\frac{1}{n}\\sum_{i=1}^{n}x_i$$'}]}]}
    with TemporaryDirectory() as folder:
        builder = ContentBuilder(content, {}, {}, folder)
        output = BytesIO()
        story = [Spacer(1, 670), *builder.markdown(content['modules'][0]['lessons'][0]['content'])]
        CourseDocument(output, 'Pagination', False).build(story)
        document = pymupdf.open(stream=output.getvalue(), filetype='pdf')
        heading_pages = [index for index, page in enumerate(document) if 'Formula heading' in page.get_text()]
        formula_pages = [index for index, page in enumerate(document) if page.get_image_info()]
        assert heading_pages == formula_pages == [1]


def test_svg_normalization_rejects_pathological_text_before_native_renderer():
    with pytest.raises(PDFRenderError, match='много текста'):
        safe_svg('<svg><text>' + 'x' * 24001 + '</text></svg>')
