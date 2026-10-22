"""Bounded local-only assets for course PDFs. No URI/network fetch is permitted."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
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


def safe_svg(source):
    if len(source.encode('utf8')) > MAX_SVG:
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
    for node in nodes:
        tag = node.tag.split('}')[-1]
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
    return ET.tostring(root, encoding='utf8')


def drawing_from_svg(source, max_width=470, max_height=570):
    data = safe_svg(source)
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
