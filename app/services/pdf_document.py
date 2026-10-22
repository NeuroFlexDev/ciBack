"""Branded, paginated course documents from immutable structured course content."""
from html import escape
from io import BytesIO
from pathlib import Path
import re
import textwrap
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from reportlab.lib import colors
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, HRFlowable, Image,
    KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle, XPreformatted)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.platypus.doctemplate import LayoutError
from svglib.fonts import register_font
from app.services.pdf_assets import (ROOT, KINDS, PDFRenderError, parser, render_inventory,
    diagram_key, drawing_from_svg, math_images, raster_asset)

INK = colors.HexColor('#191b20')
MUTED = colors.HexColor('#666d76')
LIME = colors.HexColor('#caf432')
WIDTH, HEIGHT = A4
MARGIN = 52
CONTENT_WIDTH = WIDTH - 2 * MARGIN
_FONT_LOCK = Lock()
_FONTS_READY = False


def fonts():
    global _FONTS_READY
    with _FONT_LOCK:
        if _FONTS_READY:
            return
        folder = ROOT / 'app/assets/pdf_fonts'
        for name, filename in [('Lernium', 'golos-text-400.ttf'), ('LerniumBold', 'golos-text-700.ttf'), ('LerniumDays', 'days-one.ttf')]:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, folder / filename))
        for weight in ['normal', 'bold']:
            for style in ['normal', 'italic']:
                register_font('Lernium', str(folder / ('golos-text-700.ttf' if weight == 'bold' else 'golos-text-400.ttf')),
                    weight=weight, style=style, rlgFontName='LerniumBold' if weight == 'bold' else 'Lernium')
        # SVG registration also registers TTFs and resets ReportLab family maps.
        # Establish Markdown emphasis last, once, before concurrent rendering.
        pdfmetrics.registerFontFamily('Lernium', normal='Lernium', bold='LerniumBold', italic='Lernium', boldItalic='LerniumBold')
        _FONTS_READY = True


def styles():
    base = ParagraphStyle('Body', fontName='Lernium', fontSize=10.5, leading=16.5, textColor=INK,
        spaceAfter=9, splitLongWords=True, allowWidows=0, allowOrphans=0, autoLeading='max')
    return {'body': base,
        'h1': ParagraphStyle('Module', parent=base, fontName='LerniumDays', fontSize=23, leading=31, spaceBefore=8, spaceAfter=20, keepWithNext=True),
        'h2': ParagraphStyle('Lesson', parent=base, fontName='LerniumBold', fontSize=17, leading=24, spaceBefore=18, spaceAfter=12, keepWithNext=True),
        'h3': ParagraphStyle('Section', parent=base, fontName='LerniumBold', fontSize=13, leading=19, spaceBefore=16, spaceAfter=10, keepWithNext=True),
        'small': ParagraphStyle('Caption', parent=base, fontSize=8.5, leading=13, textColor=MUTED, spaceAfter=12),
        'cell': ParagraphStyle('Cell', parent=base, fontSize=9, leading=13, spaceAfter=2),
        'code': ParagraphStyle('Code', parent=base, fontSize=8.2, leading=12, backColor=colors.HexColor('#f0f2f4'),
            borderPadding=10, spaceBefore=8, spaceAfter=14),
        'quote': ParagraphStyle('Quote', parent=base, leftIndent=15, borderColor=LIME, borderWidth=2, borderPadding=12, spaceBefore=8, spaceAfter=16),
    }


class Cover(Flowable):
    def __init__(self, content, meta):
        super().__init__()
        self.content, self.meta = content, meta
        self.width, self.height = CONTENT_WIDTH, HEIGHT - 123

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(colors.HexColor('#141414'))
        c.rect(-MARGIN, -56, WIDTH, HEIGHT, fill=1, stroke=0)
        c.setFillColor(LIME)
        c.roundRect(0, self.height - 70, 125, 36, 18, fill=1, stroke=0)
        c.setFillColor(INK); c.setFont('LerniumDays', 15)
        c.drawString(16, self.height - 57, 'LERNIUM')
        c.setFont('Lernium', 10); c.setFillColor(colors.HexColor('#b5b5bd'))
        c.drawString(0, self.height - 111, 'УЧЕБНЫЙ КУРС  /  LEARNING COURSE')
        title = self.content['course']['title']
        size = 31
        while True:
            p = Paragraph(escape(title), ParagraphStyle('Cover', fontName='LerniumDays', fontSize=size,
                leading=size * 1.35, textColor=colors.white))
            _, height = p.wrap(CONTENT_WIDTH, 350)
            if height <= 350 or size <= 15:
                break
            size -= 1
        if height > 350:
            raise PDFRenderError('Название курса слишком длинное для обложки PDF.')
        p.drawOn(c, 0, self.height - 160 - height)
        # The shutter geometry follows the platform's lime architectural motif.
        c.setStrokeColor(LIME); c.setLineWidth(1)
        for i in range(11):
            x = 5 + i * 45
            c.line(x, 105, x + 16, 195)
        c.setFillColor(colors.white); c.setFont('LerniumBold', 11)
        modules = self.content.get('modules', [])
        lessons = sum(len(module.get('lessons', [])) for module in modules)
        c.drawString(0, 68, f"Модули: {len(modules)}  ·  Уроки: {lessons}  ·  Версия {self.meta['revision']}")
        c.setFillColor(colors.HexColor('#b5b5bd')); c.setFont('Lernium', 9)
        c.drawString(0, 42, 'Опубликованная версия / Published edition')
        c.drawString(0, 25, str(self.meta.get('published_at') or '')[:10])
        if self.meta.get('includes_answer_keys'):
            c.setFillColor(LIME); c.setFont('LerniumBold', 10)
            c.drawString(0, 0, 'ДЛЯ ПРЕПОДАВАТЕЛЯ · СОДЕРЖИТ КЛЮЧИ ОТВЕТОВ')
        c.restoreState()


class VectorDiagram(Flowable):
    def __init__(self, drawing):
        super().__init__()
        self.drawing = drawing
        self.width, self.height = drawing.width, drawing.height
        self.spaceBefore, self.spaceAfter = 12, 16

    def draw(self):
        renderPDF.draw(self.drawing, self.canv, 0, 0)


class CourseDocument(BaseDocTemplate):
    def __init__(self, output, title, private):
        super().__init__(output, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=58, bottomMargin=55,
            title=title, author='Lernium', creator='Lernium Course Export', pageCompression=1)
        self.course_title, self.private = title, private
        self.addPageTemplates(PageTemplate('course', [Frame(MARGIN, 55, CONTENT_WIDTH, HEIGHT - 113,
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=self.page_chrome))

    def page_chrome(self, canvas, doc):
        if doc.page == 1:
            canvas.saveState()
            canvas.setFillColor(colors.HexColor('#141414'))
            canvas.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
            canvas.restoreState()
            return
        if doc.page > 500:
            raise PDFRenderError('PDF превышает 500 страниц. Разделите курс на части.')
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#dce1d2')); canvas.setLineWidth(.6)
        canvas.line(MARGIN, HEIGHT - 39, WIDTH - MARGIN, HEIGHT - 39)
        canvas.setFillColor(MUTED); canvas.setFont('Lernium', 8)
        title = self.course_title
        while pdfmetrics.stringWidth(title, 'Lernium', 8) > CONTENT_WIDTH - 90:
            title = title[:-2]
        canvas.drawString(MARGIN, HEIGHT - 29, title)
        canvas.setFont('LerniumBold', 8); canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 29, 'LERNIUM')
        canvas.line(MARGIN, 41, WIDTH - MARGIN, 41)
        canvas.setFont('Lernium', 7.5)
        canvas.drawString(MARGIN, 27, 'Материалы преподавателя / Instructor copy' if self.private else 'Учебные материалы / Learning materials')
        canvas.drawRightString(WIDTH - MARGIN, 27, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        entry = getattr(flowable, '_toc', None)
        if entry:
            level, title, key = entry
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level, closed=False)
            self.notify('TOCEntry', (level, escape(title), self.page, key))


class ContentBuilder:
    def __init__(self, content, images, diagrams, folder):
        self.styles = styles()
        inventory, maths = render_inventory(content)
        expected = {item['id'] for item in inventory}
        if set(diagrams) != expected:
            raise PDFRenderError('Состав схем не соответствует опубликованной версии. Обновите экспорт.')
        self.diagrams = {}
        diagram_deadline = monotonic() + 30
        for key, svg in diagrams.items():
            remaining = diagram_deadline - monotonic()
            if remaining <= 0:
                raise PDFRenderError('Схемы превысили время подготовки PDF. Разделите большой курс.')
            self.diagrams[key] = drawing_from_svg(svg, CONTENT_WIDTH, 565, normalize=True,
                timeout=min(10, remaining))
        self.maths = math_images(maths, folder)
        self.images = {name: raster_asset(data, folder, name) for name, data in images.items()}
        self.md = parser()
        self.heading_id = 0

    def heading(self, title, level):
        self.heading_id += 1
        p = Paragraph(escape(title), self.styles['h1' if level == 0 else 'h2'])
        p._toc = (level, title, f'entry-{self.heading_id}')
        return p

    def inline(self, children, max_width=CONTENT_WIDTH):
        output = []
        for token in children or []:
            if token.type in {'text', 'code_inline'}:
                output.append(escape(token.content))
            elif token.type in {'softbreak', 'hardbreak'}:
                output.append('<br/>' if token.type == 'hardbreak' else ' ')
            elif token.type in {'strong_open', 'strong_close', 'em_open', 'em_close', 's_open', 's_close'}:
                tag = {'strong': 'b', 'em': 'i', 's': 'strike'}[token.type.split('_')[0]]
                output.append(('<' if token.nesting == 1 else '</') + tag + '>')
            elif token.type == 'link_open':
                href = token.attrGet('href') or ''
                token.meta['allowed'] = urlsplit(href).scheme in {'http', 'https', 'mailto'}
                output.append('<a href="' + escape(href, quote=True) + '" color="#526b15">' if token.meta['allowed'] else '<span>')
            elif token.type == 'link_close':
                # Markdown link nesting is flat; inspect the last opened tag.
                last_open = max((i for i, value in enumerate(output) if value.startswith(('<a ', '<span>'))), default=-1)
                output.append('</a>' if last_open >= 0 and output[last_open].startswith('<a ') else '</span>')
            elif token.type == 'math_inline':
                path, width, height = self.maths[token.content]
                scale = min(1, 26 / height, max_width / width)
                output.append(f'<img src="{escape(str(path), quote=True)}" width="{width*scale}" height="{height*scale}" valign="middle"/>')
            elif token.type == 'image':
                source = token.attrGet('src') or ''
                if source not in self.images:
                    raise PDFRenderError('Внешняя или недоступная иллюстрация: загрузите её в курс перед публикацией.')
                path, width, height = self.images[source]
                scale = min(max_width / width, 350 / height, 1)
                output.append(f'<br/><img src="{escape(str(path), quote=True)}" width="{width*scale}" height="{height*scale}"/><br/>')
            else:
                output.append(escape(token.content))
        return ''.join(output)

    def markdown(self, text):
        tokens, output, index = self.md.parse(text or ''), [], 0
        lists, bullet = [], None
        quote_depth = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type in {'paragraph_open', 'heading_open'}:
                inline = tokens[index + 1]
                kind = 'h3' if token.type == 'heading_open' else ('quote' if quote_depth else 'body')
                children = inline.children or []
                if len(children) == 1 and children[0].type == 'image':
                    source = children[0].attrGet('src') or ''
                    if source not in self.images:
                        raise PDFRenderError('Внешняя иллюстрация не включена в курс. Загрузите её и опубликуйте новую версию.')
                    path, width, height = self.images[source]
                    scale = min(CONTENT_WIDTH / width, 440 / height, 1)
                    image = Image(str(path), width*scale, height*scale)
                    caption = Paragraph(escape(children[0].content), self.styles['small'])
                    output.append(KeepTogether([Spacer(1, 8), image, Spacer(1, 7), caption]))
                else:
                    style = self.styles[kind]
                    if lists:
                        style = ParagraphStyle('List', parent=style, leftIndent=16*len(lists), bulletIndent=16*(len(lists)-1), bulletFontName='Lernium')
                    output.append(Paragraph(self.inline(children), style, bulletText=bullet))
                bullet = None
                index += 2
            elif token.type in {'bullet_list_open', 'ordered_list_open'}:
                lists.append(None if token.type == 'bullet_list_open' else int(token.attrGet('start') or 1))
            elif token.type in {'bullet_list_close', 'ordered_list_close'}:
                lists.pop()
            elif token.type == 'list_item_open':
                bullet = '•' if lists[-1] is None else f'{lists[-1]}.'
                if lists[-1] is not None:
                    lists[-1] += 1
            elif token.type == 'blockquote_open':
                quote_depth += 1
            elif token.type == 'blockquote_close':
                quote_depth -= 1
            elif token.type == 'fence':
                kind = token.info.strip().lower()
                if kind in KINDS:
                    output.append(VectorDiagram(self.diagrams[diagram_key(kind, token.content)]))
                else:
                    wrapped = '\n'.join('\n'.join(textwrap.wrap(line.expandtabs(4), 88, replace_whitespace=False, drop_whitespace=False)) or ' ' for line in token.content.splitlines())
                    output.append(XPreformatted(escape(wrapped), self.styles['code']))
            elif token.type == 'math_block':
                path, width, height = self.maths[token.content]
                scale = min(1.15, CONTENT_WIDTH / width, 350 / height)
                formula = Image(str(path), width*scale, height*scale)
                # Leading Spacer breaks heading.keepWithNext at a page edge.
                # Attach spacing to the media so heading + content move together.
                formula.spaceBefore, formula.spaceAfter = 8, 14
                output.append(formula)
            elif token.type == 'hr':
                output.append(HRFlowable(width='100%', color=colors.HexColor('#d9e1cb'), spaceBefore=12, spaceAfter=12))
            elif token.type == 'table_open':
                rows, row = [], []
                index += 1
                while tokens[index].type != 'table_close':
                    current = tokens[index]
                    if current.type == 'tr_open':
                        row = []
                    elif current.type == 'inline':
                        row.append(current.children or [])
                    elif current.type == 'tr_close':
                        rows.append(row)
                    index += 1
                if rows:
                    columns = max(map(len, rows))
                    if columns > 12:
                        raise PDFRenderError('Таблица шире 12 столбцов. Разделите её перед PDF-экспортом.')
                    weights = [min(4, max(1, max(sum(len(token.content) for token in row[c]) if c < len(row) else 0 for row in rows)/18)) for c in range(columns)]
                    widths = [CONTENT_WIDTH * weight / sum(weights) for weight in weights]
                    # Render after measuring columns: media must fit the cell's
                    # actual content box, including the horizontal padding.
                    cells = [[Paragraph(self.inline(children, widths[c] - 16), self.styles['cell'])
                              for c, children in enumerate(row)] for row in rows]
                    table = Table(cells, colWidths=widths, repeatRows=1,
                        hAlign='LEFT', splitByRow=1, splitInRow=1)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e9f2d2')),
                        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f5f6f7')]),
                        ('LINEBELOW', (0,0), (-1,0), 1, colors.HexColor('#a2b77c')),
                        ('GRID', (0,0), (-1,-1), .35, colors.HexColor('#d7dce1')),
                        ('VALIGN', (0,0), (-1,-1), 'TOP'),
                        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
                        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                    ]))
                    output.extend([table, Spacer(1, 14)])
            index += 1
        return output


def build_pdf(content, images, metadata, diagrams):
    fonts()
    with TemporaryDirectory(prefix='lernium-pdf-') as folder:
        builder = ContentBuilder(content, images, diagrams, folder)
        s = builder.styles
        story = [Cover(content, metadata), PageBreak(), Paragraph('Содержание / Contents', s['h1'])]
        toc = TableOfContents()
        toc.levelStyles = [ParagraphStyle('TOCModule', parent=s['body'], fontName='LerniumBold', fontSize=11, spaceBefore=9, leading=17),
            ParagraphStyle('TOCLesson', parent=s['body'], fontSize=9.5, leftIndent=18, firstLineIndent=0, leading=15)]
        story.extend([toc, PageBreak()])
        if content['course'].get('description'):
            story.extend([Paragraph('О курсе / About this course', s['h2']), *builder.markdown(content['course']['description']), Spacer(1, 18)])
        def assessment(item):
            result = builder.markdown('### ' + item['question'])
            result.extend(builder.markdown(item.get('description', '')))
            for answer in item.get('answers', []):
                result.extend(builder.markdown('[ ]  ' + answer))
            result.append(Paragraph(f"Максимум баллов / Maximum points: {item.get('max_points', 0)}", s['small']))
            for criterion in item.get('criteria', []):
                result.append(Paragraph(escape(f"{criterion['title']} ({criterion['max_points']})"), s['h3']))
                result.extend(builder.markdown(criterion.get('description', '')))
                for level in criterion.get('levels', []):
                    result.append(Paragraph(escape(f"{level['title']} ({level['points']})"), s['body']))
                    result.extend(builder.markdown(level.get('description', '')))
            if metadata.get('includes_answer_keys') and item.get('correct_answers'):
                result.append(Paragraph('Ключ ответа / Instructor answer key', s['h3']))
                for answer in item['correct_answers']:
                    result.extend(builder.markdown(answer))
            return result
        for mi, module in enumerate(content.get('modules', [])):
            if mi:
                story.append(PageBreak())
            story.append(builder.heading(f"{mi+1:02d}. {module['title']}", 0))
            story.extend(builder.markdown(module.get('description', '')))
            for li, lesson in enumerate(module.get('lessons', [])):
                story.append(builder.heading(f"{mi+1}.{li+1}. {lesson['title']}", 1))
                story.extend(builder.markdown(lesson.get('description', '')))
                story.extend(builder.markdown(lesson.get('content', '')))
            for item in module.get('assessments', []):
                story.extend(assessment(item))
        if content.get('assessments'):
            story.extend([PageBreak(), builder.heading('Итоговая проверка / Final assessment', 0)])
            for item in content['assessments']:
                story.extend(assessment(item))
        story.extend([Spacer(1, 26), HRFlowable(width='100%', color=LIME), Spacer(1, 12),
            Paragraph(f"Версия {metadata['revision']} · SHA-256: {metadata['snapshot_sha256']}", s['small'])])
        output = BytesIO()
        doc = CourseDocument(output, content['course']['title'], metadata.get('includes_answer_keys', False))
        try:
            doc.multiBuild(story, maxPasses=4)
        except (LayoutError, ValueError, RecursionError) as exc:
            raise PDFRenderError('Не удалось разместить содержание PDF. Уменьшите большую таблицу или схему и повторите экспорт.') from exc
        data = output.getvalue()
        if len(data) > 80 * 1024 * 1024:
            raise PDFRenderError('PDF превышает допустимые 80 MiB.')
        return data
