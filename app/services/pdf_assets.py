"""Bounded local-only assets for course PDFs. No URI/network fetch is permitted."""
from hashlib import sha256
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import Element, register_namespace
import json
import re
import shutil
import subprocess
from defusedxml import ElementTree as ET
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from PIL import Image as PILImage
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

ROOT = Path(__file__).resolve().parents[2]
KINDS = {'mermaid', 'uml', 'plantuml', 'diagram'}
MAX_CONTENT = 1_500_000
MAX_SVG = 1_500_000
MAX_NORMALIZED_SVG = 12_000_000
# svglib discovers definitions using the default SVG namespace. Keeping it
# avoids silently losing clip paths when ElementTree serializes sanitized XML.
register_namespace('', 'http://www.w3.org/2000/svg')
register_namespace('xlink', 'http://www.w3.org/1999/xlink')


class PDFRenderError(ValueError):
    pass


def parser():
    return MarkdownIt('commonmark', {'html': False}).enable('table').use(dollarmath_plugin)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def diagram_key(kind, source):
    return sha256((kind + '\n' + source.strip()).encode()).hexdigest()


def render_inventory(content):
    text = list(strings(content))
    if sum(map(len, text)) > MAX_CONTENT:
        raise PDFRenderError('Курс слишком велик для одного PDF: максимум 1 500 000 символов.')
    diagrams, maths = {}, set()
    md = parser()
    for value in text:
        for token in md.parse(value):
            kind = token.info.strip().lower() if token.type == 'fence' else ''
            if kind in KINDS:
                source = token.content.strip()
                if len(source) > 12000:
                    raise PDFRenderError('Схема превышает допустимый размер. Разделите её перед экспортом.')
                key = diagram_key(kind, source)
                diagrams[key] = {'id': key, 'kind': kind, 'source': source}
            if token.type == 'math_block':
                maths.add(token.content)
            for child in token.children or []:
                if child.type == 'math_inline':
                    maths.add(child.content)
    if len(diagrams) > 80 or len(maths) > 300 or any(len(value) > 8000 for value in maths):
        raise PDFRenderError('Превышен лимит схем или формул для одного PDF.')
    return list(diagrams.values()), sorted(maths)


def safe_svg(source, *, max_bytes=MAX_SVG, allow_use=True):
    if len(source.encode('utf8')) > max_bytes:
        raise PDFRenderError('Схема слишком велика для безопасного экспорта.')
    try:
        root = ET.fromstring(source, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise PDFRenderError('Некорректный SVG схемы.') from exc
    allowed = {'svg', 'g', 'path', 'rect', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'text', 'tspan',
               'defs', 'marker', 'clipPath', 'linearGradient', 'radialGradient', 'stop', 'use', 'title', 'desc', 'style'}
    nodes = list(root.iter())
    if len(nodes) > 20000 or root.tag.split('}')[-1] != 'svg':
        raise PDFRenderError('Некорректная структура SVG.')
    if sum(len(node.text or '') + len(node.tail or '') for node in nodes
           if node.tag.split('}')[-1] in {'text', 'tspan'}) > 24000:
        raise PDFRenderError('Слишком много текста в одной схеме.')
    for node in nodes:
        tag = node.tag.split('}')[-1]
        if tag == 'use' and not allow_use:
            raise PDFRenderError('SVG схемы не должен содержать ссылки use. Обновите схему в редакторе.')
        if tag not in allowed:
            raise PDFRenderError('SVG содержит неподдерживаемые внешние или исполняемые элементы.')
        for key, value in list(node.attrib.items()):
            attr = key.split('}')[-1].lower()
            if attr.startswith('on') or attr in {'src', 'base'} or (attr == 'href' and not re.fullmatch(r'#[A-Za-z_][\w:.-]*', value)):
                raise PDFRenderError('SVG не должен обращаться к внешним ресурсам.')
            urls = re.findall(r'url\((.*?)\)', value, re.I)
            if any(not re.fullmatch(r'#[A-Za-z_][\w:.-]*', url.strip().strip("'\"")) for url in urls) or re.search(r'@import|javascript:|expression\(', value, re.I):
                raise PDFRenderError('SVG содержит внешнюю ссылку.')
            if 'currentColor' in value:
                node.set(key, value.replace('currentColor', '#17191d'))
            if attr == 'stroke-dasharray' and value.strip().lower() != 'none':
                try:
                    lengths = [float(part.removesuffix('px')) for part in re.split(r'[\s,]+', value.strip())]
                    if any(length < 0 for length in lengths):
                        raise ValueError('negative dash')
                    if not any(lengths):
                        node.set(key, 'none')
                except ValueError as exc:
                    raise PDFRenderError('Некорректный штрих линии SVG.') from exc
        if tag == 'style' and re.search(r'@import|url\(|javascript:|expression\(', node.text or '', re.I):
            raise PDFRenderError('SVG CSS содержит внешние ресурсы.')
        if tag in {'text', 'tspan'}:
            node.set('font-family', 'Lernium')
            node.set('font-weight', 'bold' if node.get('font-weight') in {'bold', '600', '700', '800', '900'} else 'normal')
    return ET.tostring(root, encoding='utf-8')


def drawing_from_svg(source, max_width=470, max_height=570, *, normalize=False, timeout=10):
    # The browser's diagram sanitizer forbids use too. Reject references before
    # native normalization can expand a small nested graph into many paths.
    # Locally generated MathJax SVG uses the separate non-normalized path.
    data = safe_svg(source, allow_use=not normalize)
    if normalize:
        data = svg1_marker_orientation(data)
        node = shutil.which('node')
        if not node:
            raise PDFRenderError('Сервис схем PDF не установлен. Обратитесь к администратору.')
        try:
            result = subprocess.run([node, '--max-old-space-size=192', str(ROOT / 'pdf_runtime/diagram.mjs')],
                input=data.decode('utf8'), text=True, encoding='utf8', capture_output=True,
                timeout=timeout, cwd=ROOT / 'pdf_runtime', check=True)
            if len(result.stdout.encode('utf8')) > MAX_NORMALIZED_SVG + 100000:
                raise ValueError('normalized diagram size')
            normalized = json.loads(result.stdout)
            data = safe_svg(normalized['svg'], max_bytes=MAX_NORMALIZED_SVG)
        except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
            raise PDFRenderError('Не удалось точно отрисовать схему. Проверьте её и повторите экспорт.') from exc
    try:
        drawing = svg2rlg(BytesIO(data))
        if not drawing or not 0 < drawing.width < 100000 or not 0 < drawing.height < 100000:
            raise ValueError('dimensions')
        scale = min(1, max_width / drawing.width, max_height / drawing.height)
        drawing.scale(scale, scale)
        drawing.width *= scale
        drawing.height *= scale
        return drawing
    except Exception as exc:
        raise PDFRenderError('Не удалось отрисовать схему. Проверьте её в редакторе.') from exc


def svg1_marker_orientation(data):
    """Preserve SVG2 start/end orientation in resvg's SVG1.1 normalizer.

    resvg 2.6.2 treats auto-start-reverse as zero degrees. End/mid markers use
    ordinary auto. A separate start marker rotates its coordinate system by 180
    degrees, including viewBox, reference and clipping viewport, so clipping and
    off-center reference points remain equivalent to the SVG2 operation.
    """
    root = ET.fromstring(data)
    ns = '{http://www.w3.org/2000/svg}'
    markers = [node for node in root.iter() if node.tag.split('}')[-1] == 'marker'
               and node.get('orient') == 'auto-start-reverse']
    if not markers:
        return data
    ids = {node.get('id') for node in root.iter()}
    replacements = {}
    start_references = set()
    reference_pattern = r'url\(\s*([\'\"]?)#([\w:.-]+)\1\s*\)'
    for node in root.iter():
        values = [node.get('marker-start', '')]
        values.extend(match[1] for match in re.findall(r'(?i)\b(marker-start|marker)\s*:\s*([^;]+)', node.get('style', '')))
        start_references.update(match[1] for value in values for match in re.findall(reference_pattern, value))
    clones = [marker for marker in markers if marker.get('id') in start_references]
    if sum(1 for _ in root.iter()) + sum(sum(1 for _ in marker.iter()) + 1 for marker in clones) > 20000:
        raise PDFRenderError('Слишком сложные наконечники SVG для экспорта.')

    def number(value):
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:px)?', value):
            raise PDFRenderError('Неподдерживаемые координаты наконечника SVG.')
        result = float(value.removesuffix('px'))
        if not -100000 < result < 100000:
            raise PDFRenderError('Неподдерживаемые координаты наконечника SVG.')
        return result

    for index, marker in enumerate(markers):
        original_id = marker.get('id')
        if not original_id or original_id not in start_references:
            marker.set('orient', 'auto')
            continue
        clone_id = f'lernium-pdf-reversed-marker-{index}'
        while clone_id in ids:
            clone_id += '-x'
        ids.add(clone_id)
        clone = deepcopy(marker)
        clone.set('id', clone_id)
        clone.set('orient', 'auto')
        local_ids = {child.get('id'): f'{clone_id}-child-{child_index}'
                     for child_index, child in enumerate(list(clone.iter())[1:]) if child.get('id')}
        for child in list(clone.iter())[1:]:
            if child.get('id') in local_ids:
                child.set('id', local_ids[child.get('id')])
            for key, value in list(child.attrib.items()):
                value = re.sub(reference_pattern,
                    lambda match: f'url(#{local_ids.get(match[2], match[2])})', value)
                if key.split('}')[-1] == 'href' and value[1:] in local_ids:
                    value = '#' + local_ids[value[1:]]
                child.set(key, value)
        view_box = marker.get('viewBox')
        coordinates = [number(value) for value in re.split(r'[\s,]+', view_box.strip())] if view_box else [
            0, 0, number(marker.get('markerWidth', '3')), number(marker.get('markerHeight', '3'))]
        if len(coordinates) != 4 or coordinates[2] <= 0 or coordinates[3] <= 0:
            raise PDFRenderError('Некорректный viewport наконечника SVG.')
        x, y, width, height = coordinates
        clone.set('viewBox', f'{-x-width:g} {-y-height:g} {width:g} {height:g}')
        clone.set('refX', f'{-number(marker.get("refX", "0")):g}')
        clone.set('refY', f'{-number(marker.get("refY", "0")):g}')
        alignment = marker.get('preserveAspectRatio')
        if alignment:
            clone.set('preserveAspectRatio', re.sub(r'[xy](?:Min|Max)',
                lambda match: match[0][0] + ('Max' if match[0][1:] == 'Min' else 'Min'), alignment))
        group = Element(ns + 'g', {'transform': 'rotate(180)'})
        for child in list(clone):
            clone.remove(child)
            group.append(child)
        clone.append(group)
        root.append(clone)
        marker.set('orient', 'auto')
        replacements[original_id] = clone_id

    def reverse_reference(value):
        return re.sub(reference_pattern,
            lambda match: f'url(#{replacements.get(match[2], match[2])})', value)

    for node in root.iter():
        if node.get('marker-start'):
            node.set('marker-start', reverse_reference(node.get('marker-start')))
        if node.get('style'):
            def declaration(match):
                name, value = match[1].lower(), match[2]
                if name == 'marker':
                    return f'marker-start:{reverse_reference(value)};marker-mid:{value};marker-end:{value}'
                return f'marker-start:{reverse_reference(value)}'
            node.set('style', re.sub(r'(?i)\b(marker-start|marker)\s*:\s*([^;]+)', declaration, node.get('style')))
    return safe_svg(ET.tostring(root, encoding='unicode'), max_bytes=MAX_NORMALIZED_SVG)


def math_images(expressions, folder):
    if not expressions:
        return {}
    node = shutil.which('node')
    if not node:
        raise PDFRenderError('Сервис формул PDF не установлен. Обратитесь к администратору.')
    try:
        result = subprocess.run([node, '--max-old-space-size=256', str(ROOT / 'pdf_runtime/math.mjs')],
            input=json.dumps(expressions), text=True, encoding='utf8', capture_output=True, timeout=25,
            cwd=ROOT / 'pdf_runtime', check=True)
        output = json.loads(result.stdout)
        if len(output) != len(expressions):
            raise ValueError('count')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PDFRenderError('Не удалось отрисовать формулу. Проверьте LaTeX и повторите экспорт.') from exc
    values = {}
    for index, (source, svg) in enumerate(zip(expressions, output)):
        # MathJax uses ex units; explicit points keep the SVG aspect and baseline.
        root = ET.fromstring(svg, forbid_dtd=True)
        _, _, width, height = map(float, root.attrib['viewBox'].split())
        root.set('width', str(width * .012)); root.set('height', str(height * .012))
        drawing = drawing_from_svg(ET.tostring(root, encoding='unicode'))
        path = Path(folder) / f'math-{index}.png'
        renderPM.drawToFile(drawing, str(path), fmt='PNG', dpi=240, backend='_renderPM')
        values[source] = (path, drawing.width, drawing.height)
    return values


def raster_asset(data, folder, name):
    try:
        image = PILImage.open(BytesIO(data))
        if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 24_000_000:
            raise ValueError('dimensions')
        image.load()
        path = Path(folder) / (sha256(name.encode()).hexdigest() + '.png')
        image.convert('RGBA' if 'A' in image.getbands() else 'RGB').save(path, 'PNG')
        return path, image.width, image.height
    except Exception as exc:
        raise PDFRenderError('Не удалось прочитать иллюстрацию. Неполный PDF не выдан.') from exc
