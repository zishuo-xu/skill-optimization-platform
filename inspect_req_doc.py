"""Dump the requirements document's structure and text for review."""
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn


def iter_block_items(parent):
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


doc = Document("Skill优化平台需求文档_v1.docx")
print("=== CORE PROPERTIES ===")
cp = doc.core_properties
print("title:", cp.title)
print("subject:", cp.subject)
print("author:", cp.author)
print("created:", cp.created)
print("modified:", cp.modified)
print()
print("=== BODY ===")
i = 0
for block in iter_block_items(doc):
    if isinstance(block, Paragraph):
        text = block.text
        style = block.style.name if block.style is not None else ""
        if text.strip():
            print(f"[P{i}] <{style}> {text}")
        # detect images
        if block._p.findall(".//" + qn("a:blip")):
            print(f"[P{i}] <IMAGE>")
        i += 1
    else:
        print(f"[TABLE] rows={len(block.rows)} cols={len(block.columns)}")
        for r, row in enumerate(block.rows):
            cells = []
            for c in row.cells:
                cells.append(c.text.replace("\n", " / ").strip())
            print("   R%d: %s" % (r, " | ".join(cells)))
print()
print("=== SECTIONS ===")
for s in doc.sections:
    print("page", s.page_width, s.page_height, "margins", s.left_margin, s.right_margin, s.top_margin, s.bottom_margin)
print()
print("=== INLINE SHAPES ===", len(doc.inline_shapes))
