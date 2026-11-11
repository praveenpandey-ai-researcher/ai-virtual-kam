"""Shared PDF styling for all project documents (PRD, tech note, prep book, demo script)."""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, ListFlowable, ListItem)

# Arial ships with Windows and includes the rupee glyph.
pdfmetrics.registerFont(TTFont("Body", "C:/Windows/Fonts/arial.ttf"))
pdfmetrics.registerFont(TTFont("Body-Bold", "C:/Windows/Fonts/arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Body-Italic", "C:/Windows/Fonts/ariali.ttf"))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
                              boldItalic="Body-Bold")

INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5F6B7A")
ACCENT = colors.HexColor("#B4232A")
NAVY = colors.HexColor("#1E3A5F")
RULE = colors.HexColor("#D5DBE3")
TINT = colors.HexColor("#F2F5F9")
NOTE_BG = colors.HexColor("#FFF6E5")
NOTE_BORDER = colors.HexColor("#E8B04B")

S = {
    "title": ParagraphStyle("title", fontName="Body-Bold", fontSize=22, leading=27, textColor=INK,
                            spaceAfter=4),
    "subtitle": ParagraphStyle("subtitle", fontName="Body", fontSize=11, leading=15,
                               textColor=MUTED, spaceAfter=10),
    "h1": ParagraphStyle("h1", fontName="Body-Bold", fontSize=14, leading=18, textColor=NAVY,
                         spaceBefore=14, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="Body-Bold", fontSize=11, leading=14, textColor=INK,
                         spaceBefore=8, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Body", fontSize=9.5, leading=13.5, textColor=INK,
                           alignment=TA_LEFT, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Body", fontSize=8, leading=10.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Body", fontSize=8.5, leading=11, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Body-Bold", fontSize=8.5, leading=11,
                            textColor=colors.white),
    "quote": ParagraphStyle("quote", fontName="Body-Italic", fontSize=9.5, leading=13.5,
                            textColor=INK, leftIndent=10),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def h1(text):
    return P(text, "h1")


def h2(text):
    return P(text, "h2")


def bullets(items, style="body"):
    return ListFlowable(
        [ListItem(P(i, style), leftIndent=12, value="circle") for i in items],
        bulletType="bullet", bulletFontSize=6, bulletOffsetY=-2, leftIndent=12,
        spaceBefore=0, spaceAfter=4)


def table(rows, widths, header=True, zebra=True):
    """rows: list of lists of strings (markup allowed). First row is the header."""
    data = []
    for r, row in enumerate(rows):
        st = "cellb" if (header and r == 0) else "cell"
        data.append([Paragraph(str(c), S[st]) for c in row])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
    ]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    if zebra:
        for r in range(1 if header else 0, len(rows)):
            if r % 2 == 0:
                cmds.append(("BACKGROUND", (0, r), (-1, r), TINT))
    t.setStyle(TableStyle(cmds))
    return t


def callout(text, title=None):
    """Highlighted box for key insights / decisions."""
    content = f"<b>{title}</b><br/>{text}" if title else text
    t = Table([[Paragraph(content, S["body"])]], colWidths=[174 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 3, NOTE_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether([Spacer(1, 3), t, Spacer(1, 6)])


def build(path, story, doc_title):
    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setStrokeColor(ACCENT)
        canvas.setLineWidth(2)
        canvas.line(18 * mm, h - 12 * mm, w - 18 * mm, h - 12 * mm)
        canvas.setFont("Body", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, h - 10 * mm, "Mini AI Voice KAM  |  Snapdeal AI First Intern assignment")
        canvas.drawRightString(w - 18 * mm, h - 10 * mm, doc_title)
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm, title=doc_title,
                            author="Mini AI Voice KAM")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


__all__ = ["P", "h1", "h2", "bullets", "table", "callout", "build", "Spacer", "mm", "KeepTogether"]
