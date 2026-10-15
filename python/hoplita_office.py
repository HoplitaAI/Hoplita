import copy
import datetime
import hashlib
import html as _h
import io
import os
import re
import shutil
import tempfile
import zipfile

KINDS = {".docx": "docx", ".docm": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx",
         ".pptx": "pptx", ".pptm": "pptx"}
_NOT_OOXML = {".doc": "Word 97-2003", ".xls": "Excel 97-2003",
              ".ppt": "PowerPoint 97-2003", ".odt": "OpenDocument text",
              ".ods": "OpenDocument spreadsheet",
              ".odp": "OpenDocument presentation", ".rtf": "RTF"}
OFFICE_EXTS = set(KINDS) | set(_NOT_OOXML)

_CAP_TEXT = 500
_CAP_BLOCKS = 200
_VIEW_ROWS, _VIEW_COLS = 300, 40


class _StepError(Exception):
    pass


def kind_of(path):
    return KINDS.get(os.path.splitext(str(path or ""))[1].lower())


def _check_source(path):
    p = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not path:
        return None, "source path required"
    ext = os.path.splitext(p)[1].lower()
    if ext in _NOT_OOXML:
        return None, (f"'{os.path.basename(p)}' is {_NOT_OOXML[ext]}, not a "
                      "Word/Excel/PowerPoint OOXML file (.docx/.xlsx/.pptx). "
                      "Convert it first (LibreOffice: soffice --headless "
                      "--convert-to docx|xlsx|pptx).")
    k = KINDS.get(ext)
    if not k:
        return None, (f"'{os.path.basename(p)}' is not an office file "
                      "(.docx .docm .xlsx .xlsm .pptx .pptm).")
    if not os.path.isfile(p):
        return None, f"'{p}' does not exist."
    if not zipfile.is_zipfile(p):
        return None, (f"'{os.path.basename(p)}' is not a readable OOXML "
                      "package: it is password-protected, damaged, or an old "
                      "binary file renamed.")
    return p, k


_LIBS = {"docx": ("docx", "python-docx"), "xlsx": ("openpyxl", "openpyxl"),
         "pptx": ("pptx", "python-pptx")}


def _missing_lib(kind):
    import importlib.util
    import sys
    mod, pkg = _LIBS[kind]
    if importlib.util.find_spec(mod) is not None:
        return None
    return (f"{pkg} is not installed in the Python that runs HOPLITA. "
            f"Install it with: {sys.executable} -m pip install {pkg}")


def _open(path, kind, data_only=False):
    if kind == "docx":
        import docx
        return docx.Document(path)
    if kind == "xlsx":
        import openpyxl
        return openpyxl.load_workbook(
            path, data_only=data_only,
            keep_vba=os.path.splitext(path)[1].lower() == ".xlsm")
    import pptx
    return pptx.Presentation(path)


def _parts_by_kind(path):
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("/") or n.startswith("docProps/") \
                        or n == "[Content_Types].xml" or n.endswith("calcChain.xml"):
                    continue
                k = re.sub(r"\d+", "#", n)
                out[k] = out.get(k, 0) + 1
    except Exception:
        pass
    return out


def _lost(before, after):
    return {k: (n, after.get(k, 0)) for k, n in before.items()
            if after.get(k, 0) < n}


def _base_dir():
    try:
        import hoplita_paths
        return hoplita_paths.hoplita_base()
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".hoplita")


def _replace_in_runs(runs, find, repl, match_case=True):
    if not find or not runs:
        return 0
    texts = [r.text or "" for r in runs]
    full = "".join(texts)
    hay, needle = (full, find) if match_case else (full.lower(), find.lower())
    starts, i = [], hay.find(needle)
    while i >= 0:
        starts.append(i)
        i = hay.find(needle, i + len(needle))
    for s in reversed(starts):
        e = s + len(find)
        bounds, acc = [], 0
        for t in texts:
            bounds.append((acc, acc + len(t)))
            acc += len(t)
        ri = next(k for k, (a, b) in enumerate(bounds) if a <= s < b)
        rj = next(k for k, (a, b) in enumerate(bounds) if a < e <= b)
        a_i, a_j = bounds[ri][0], bounds[rj][0]
        if ri == rj:
            t = texts[ri]
            texts[ri] = t[:s - a_i] + repl + t[e - a_i:]
        else:
            texts[ri] = texts[ri][:s - a_i] + repl
            for k in range(ri + 1, rj):
                texts[k] = ""
            texts[rj] = texts[rj][e - a_j:]
    for r, t in zip(runs, texts):
        if (r.text or "") != t:
            r.text = t
    return len(starts)


def _count(text, find, match_case):
    if not find:
        return 0
    return (text or "").count(find) if match_case \
        else (text or "").lower().count(find.lower())


def _docx_runs(par):
    out = []
    try:
        from docx.text.run import Run
        for item in par.iter_inner_content():
            if isinstance(item, Run):
                out.append(item)
            else:
                out.extend(getattr(item, "runs", []) or [])
    except Exception:
        out = list(par.runs)
    return out


def _docx_blocks(doc):
    from docx.table import Table
    return [("table", b) if isinstance(b, Table) else ("p", b)
            for b in doc.iter_inner_content()]


def _docx_all_paragraphs(doc):
    seen = set()

    def walk_tables(tables):
        for t in tables:
            for row in t.rows:
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    yield from cell.paragraphs
                    yield from walk_tables(cell.tables)

    yield from doc.paragraphs
    yield from walk_tables(doc.tables)
    for s in doc.sections:
        for hf in (s.header, s.footer, s.first_page_header,
                   s.first_page_footer, s.even_page_header, s.even_page_footer):
            try:
                if hf.is_linked_to_previous:
                    continue
                yield from hf.paragraphs
                yield from walk_tables(hf.tables)
            except Exception:
                continue


def _docx_info(doc, args):
    blocks = _docx_blocks(doc)
    rng = _parse_range(args.get("range"), len(blocks))
    if isinstance(rng, str):
        return {"error": rng}
    out_blocks, ti, empty = [], 0, 0
    tindex = {}
    for i, (k, b) in enumerate(blocks):
        if k == "table":
            tindex[i] = ti
            ti += 1
    for i, (k, b) in enumerate(blocks):
        if rng is None and len(out_blocks) >= _CAP_BLOCKS:
            break
        if rng is not None and i not in rng:
            continue
        if k == "p":
            if not b.text.strip():
                empty += 1
                continue
            out_blocks.append({"i": i, "style": b.style.name if b.style else "",
                               "text": b.text[:_CAP_TEXT]})
        else:
            rows = [[c.text[:120] for c in r.cells] for r in b.rows[:12]]
            out_blocks.append({"i": i, "table": tindex[i], "rows": len(b.rows),
                               "cols": len(b.columns), "cells": rows})
    out = {"kind": "docx", "blocks": len(blocks),
           "paragraphs": sum(1 for k, _ in blocks if k == "p"),
           "tables": ti, "shown": out_blocks}
    if empty:
        out["empty_paragraphs_skipped"] = empty
    if rng is None and len(blocks) > _CAP_BLOCKS:
        out["more"] = (f"{len(blocks)} blocks; shown up to {_CAP_BLOCKS}. "
                       "Ask range='a-b' for others, or find='text'.")
    try:
        cp = doc.core_properties
        out["meta"] = {k: str(v) for k, v in (("title", cp.title),
                       ("author", cp.author), ("modified", cp.modified))
                       if v}
    except Exception:
        pass
    out["styles"] = sorted({s.name for s in doc.styles
                            if getattr(s, "type", None) == 1})[:40]
    return out


def _docx_find(doc, find, match_case):
    hits = []
    for i, (k, b) in enumerate(_docx_blocks(doc)):
        texts = [b.text] if k == "p" else \
            [c.text for r in b.rows for c in r.cells]
        if any(_count(t, find, match_case) for t in texts):
            hits.append({"i": i, "type": "table" if k == "table" else "p",
                         "text": (texts[0] if k == "p" else
                                  next(t for t in texts
                                       if _count(t, find, match_case)))[:300]})
    return hits


def _docx_set_par_text(par, text):
    runs = _docx_runs(par)
    if not runs:
        par.add_run(text)
        return
    runs[0].text = text
    keep = runs[0]._r
    for child in list(par._p):
        tag = child.tag.rsplit("}", 1)[-1]
        if child is keep:
            continue
        if tag in ("r", "hyperlink"):
            par._p.remove(child)
    if keep.getparent() is not par._p:
        par._p.append(keep)


def _docx_par_at(doc, idx):
    blocks = _docx_blocks(doc)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        raise _StepError("index must be a block number from office_info")
    if not 0 <= idx < len(blocks):
        raise _StepError(f"block {idx} does not exist (0..{len(blocks) - 1})")
    return blocks[idx]


def _docx_style(doc, name):
    if not name:
        return None
    try:
        doc.styles[name]
        return name
    except KeyError:
        names = sorted({s.name for s in doc.styles
                        if getattr(s, "type", None) == 1})
        raise _StepError(f"style '{name}' does not exist here; paragraph "
                         f"styles: {', '.join(names[:30])}")


def _docx_table(doc, n):
    try:
        return doc.tables[int(n)]
    except (TypeError, ValueError, IndexError):
        raise _StepError(f"table {n} does not exist (0..{len(doc.tables) - 1})")


def _w_replace(doc, p):
    find, repl = str(p.get("find") or ""), str(p.get("replace") or "")
    mc = p.get("match_case", True) is not False
    if not find:
        raise _StepError("find is empty")
    n = seen = 0
    for par in _docx_all_paragraphs(doc):
        seen += _count(par.text, find, mc)
        n += _replace_in_runs(_docx_runs(par), find, repl, mc)
    note = f"{n} replaced"
    if seen > n:
        note += (f"; {seen - n} more inside fields or content controls "
                 "were left as they are")
    if not n:
        raise _StepError(f"'{find}' does not occur in the document")
    return note


def _w_set_paragraph(doc, p):
    k, b = _docx_par_at(doc, p.get("index"))
    if k != "p":
        raise _StepError(f"block {p.get('index')} is a table, not a paragraph")
    old = b.text
    _docx_set_par_text(b, str(p.get("text") or ""))
    if p.get("style"):
        b.style = _docx_style(doc, p["style"])
    return f"block {p.get('index')}: «{old[:60]}» → «{b.text[:60]}»"


def _w_insert(doc, p, where):
    k, b = _docx_par_at(doc, p.get("index"))
    style = _docx_style(doc, p.get("style"))
    new = doc.add_paragraph(str(p.get("text") or ""), style=style)
    if style is None and k == "p" and b.style is not None:
        new.style = b.style
    el = b._element
    (el.addprevious if where == "before" else el.addnext)(new._p)
    return f"{where} block {p.get('index')}: «{new.text[:60]}»"


def _w_append(doc, p):
    new = doc.add_paragraph(str(p.get("text") or ""),
                            style=_docx_style(doc, p.get("style")))
    return f"at the end: «{new.text[:60]}»"


def _w_delete(doc, p):
    k, b = _docx_par_at(doc, p.get("index"))
    text = b.text if k == "p" else "(table)"
    b._element.getparent().remove(b._element)
    return f"block {p.get('index')} deleted: «{text[:60]}»"


def _w_cell(doc, p):
    t = _docx_table(doc, p.get("table"))
    try:
        cell = t.cell(int(p.get("row")), int(p.get("col")))
    except (TypeError, ValueError, IndexError):
        raise _StepError(f"cell ({p.get('row')}, {p.get('col')}) is outside "
                         f"table {p.get('table')} ({len(t.rows)}x{len(t.columns)})")
    pars = cell.paragraphs
    _docx_set_par_text(pars[0], str(p.get("text") or ""))
    for extra in pars[1:]:
        extra._p.getparent().remove(extra._p)
    return f"table {p.get('table')} ({p.get('row')}, {p.get('col')}) = «{str(p.get('text'))[:60]}»"


def _w_add_row(doc, p):
    t = _docx_table(doc, p.get("table"))
    vals = list(p.get("values") or [])
    row = t.add_row()
    for c, v in zip(row.cells, vals):
        c.text = "" if v is None else str(v)
    return f"table {p.get('table')}: row {len(t.rows) - 1} added"


def _w_add_table(doc, p):
    rows = p.get("rows") or []
    if not rows or not isinstance(rows, list):
        raise _StepError("rows must be a list of lists")
    ncols = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncols)
    style = p.get("style") or "Table Grid"
    try:
        t.style = style
    except Exception:
        pass
    for r, vals in zip(t.rows, rows):
        for c, v in zip(r.cells, vals):
            c.text = "" if v is None else str(v)
    if p.get("after") is not None:
        _k, b = _docx_par_at(doc, p.get("after"))
        b._element.addnext(t._tbl)
        where = f"after block {p.get('after')}"
    else:
        where = "at the end"
    return f"{len(rows)}x{ncols} table {where}"


_W_VERBS = {
    "replace": _w_replace, "set_paragraph": _w_set_paragraph,
    "insert_before": lambda d, p: _w_insert(d, p, "before"),
    "insert_after": lambda d, p: _w_insert(d, p, "after"),
    "append": _w_append, "delete": _w_delete, "set_cell": _w_cell,
    "add_row": _w_add_row, "add_table": _w_add_table,
}


def _sheet(wb, name):
    if name in (None, ""):
        return wb.active
    if name in wb.sheetnames:
        return wb[name]
    low = {n.lower(): n for n in wb.sheetnames}
    if str(name).lower() in low:
        return wb[low[str(name).lower()]]
    raise _StepError(f"sheet '{name}' does not exist; sheets: "
                     f"{', '.join(wb.sheetnames)}")


def _xl_value(v):
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def _xlsx_info(path, args):
    import openpyxl
    from openpyxl.utils import get_column_letter
    wbf = _open(path, "xlsx")
    wbv = openpyxl.load_workbook(path, data_only=True)
    sheets = []
    for ws in wbf.worksheets:
        sheets.append({"name": ws.title, "dims": ws.dimensions,
                       "rows": ws.max_row, "cols": ws.max_column,
                       "state": ws.sheet_state})
    out = {"kind": "xlsx", "sheets": sheets,
           "active": wbf.active.title if wbf.active else ""}
    try:
        out["sheet"] = _sheet(wbf, args.get("sheet")).title
    except _StepError as e:
        return {"error": str(e)}
    ws_f, ws_v = wbf[out["sheet"]], wbv[out["sheet"]]
    rng = (args.get("range") or "").strip()
    if not rng:
        last_c = get_column_letter(min(ws_f.max_column, 15))
        rng = f"A1:{last_c}{min(ws_f.max_row, 30)}"
        if ws_f.max_row > 30 or ws_f.max_column > 15:
            out["more"] = (f"sheet is {ws_f.dimensions}; shown {rng}. "
                           "Ask range='A31:O60' for more.")
    try:
        cells_f = ws_f[rng]
        cells_v = ws_v[rng]
    except Exception:
        return {"error": f"range '{rng}' is not a cell range like A1:D20"}
    if not isinstance(cells_f, tuple):
        cells_f, cells_v = ((cells_f,),), ((cells_v,),)
    elif cells_f and not isinstance(cells_f[0], tuple):
        cells_f, cells_v = (cells_f,), (cells_v,)
    grid, formulas = [], {}
    for rf, rv in zip(cells_f, cells_v):
        row = []
        for cf, cv in zip(rf, rv):
            row.append(_xl_value(cv.value))
            if isinstance(cf.value, str) and cf.value.startswith("="):
                formulas[cf.coordinate] = cf.value
        grid.append(row)
    out["range"] = rng
    out["values"] = grid
    if formulas:
        out["formulas"] = formulas
        if any(v is None for r in grid for v in r) and formulas:
            out["note"] = ("A formula whose value shows as null has never been "
                           "calculated: it gets its value when the file is "
                           "opened in Excel or LibreOffice.")
    merged = [str(m) for m in ws_f.merged_cells.ranges][:30]
    if merged:
        out["merged"] = merged
    return out


def _xlsx_find(path, find, match_case):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    hits = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and _count(c.value, find, match_case):
                    hits.append({"sheet": ws.title, "cell": c.coordinate,
                                 "text": c.value[:200]})
                    if len(hits) >= 200:
                        return hits
    return hits


def _x_set(wb, p):
    ws = _sheet(wb, p.get("sheet"))
    cell = str(p.get("cell") or "")
    try:
        ws[cell].value = p.get("value")
    except Exception:
        raise _StepError(f"'{cell}' is not a cell like B3")
    return f"{ws.title}!{cell} = {str(p.get('value'))[:60]}"


def _x_set_range(wb, p):
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    ws = _sheet(wb, p.get("sheet"))
    rows = p.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise _StepError("rows must be a list of lists")
    try:
        col_l, r0 = coordinate_from_string(str(p.get("start") or "A1"))
    except Exception:
        raise _StepError(f"start '{p.get('start')}' is not a cell like A1")
    c0 = column_index_from_string(col_l)
    n = 0
    for i, vals in enumerate(rows):
        for j, v in enumerate(vals if isinstance(vals, list) else [vals]):
            ws.cell(row=r0 + i, column=c0 + j, value=v)
            n += 1
    return f"{ws.title}: {n} cells from {p.get('start') or 'A1'}"


def _col_index(v):
    from openpyxl.utils.cell import column_index_from_string
    if isinstance(v, int):
        return v
    s = str(v or "").strip()
    return int(s) if s.isdigit() else column_index_from_string(s.upper())


_MOVE_NOTE = (" — formulas that point at moved cells are NOT rewritten "
              "(check them with office_info)")


def _x_rows(wb, p, insert):
    ws = _sheet(wb, p.get("sheet"))
    at, n = int(p.get("at") or 0), int(p.get("count") or 1)
    if at < 1:
        raise _StepError("at must be a row number (1-based)")
    (ws.insert_rows if insert else ws.delete_rows)(at, n)
    return (f"{ws.title}: {n} row(s) {'inserted' if insert else 'deleted'} at {at}"
            + _MOVE_NOTE)


def _x_cols(wb, p, insert):
    ws = _sheet(wb, p.get("sheet"))
    try:
        at = _col_index(p.get("at"))
    except Exception:
        raise _StepError("at must be a column letter or number")
    n = int(p.get("count") or 1)
    (ws.insert_cols if insert else ws.delete_cols)(at, n)
    return (f"{ws.title}: {n} column(s) {'inserted' if insert else 'deleted'} "
            f"at {p.get('at')}" + _MOVE_NOTE)


def _x_add_sheet(wb, p):
    name = str(p.get("name") or "").strip()
    if not name:
        raise _StepError("name required")
    if name in wb.sheetnames:
        raise _StepError(f"sheet '{name}' already exists")
    idx = p.get("index")
    wb.create_sheet(name, int(idx) if idx is not None else None)
    return f"sheet '{name}' added"


def _x_rename(wb, p):
    ws = _sheet(wb, p.get("sheet"))
    old, ws.title = ws.title, str(p.get("name") or "").strip() or ws.title
    return f"sheet '{old}' → '{ws.title}' (formulas naming '{old}' are NOT rewritten)"


def _x_delete_sheet(wb, p):
    ws = _sheet(wb, p.get("sheet"))
    if len(wb.sheetnames) == 1:
        raise _StepError("a workbook cannot lose its only sheet")
    wb.remove(ws)
    return f"sheet '{ws.title}' deleted"


def _x_replace(wb, p):
    find, repl = str(p.get("find") or ""), str(p.get("replace") or "")
    mc = p.get("match_case", True) is not False
    if not find:
        raise _StepError("find is empty")
    sheets = [_sheet(wb, p["sheet"])] if p.get("sheet") else wb.worksheets
    n = 0
    for ws in sheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str) or v.startswith("="):
                    continue
                k = _count(v, find, mc)
                if k:
                    c.value = v.replace(find, repl) if mc else \
                        re.sub(re.escape(find), lambda _m: repl, v, flags=re.I)
                    n += k
    if not n:
        raise _StepError(f"'{find}' does not occur in any text cell")
    return f"{n} replaced (formulas untouched)"


_X_VERBS = {
    "set": _x_set, "set_range": _x_set_range,
    "insert_rows": lambda w, p: _x_rows(w, p, True),
    "delete_rows": lambda w, p: _x_rows(w, p, False),
    "insert_cols": lambda w, p: _x_cols(w, p, True),
    "delete_cols": lambda w, p: _x_cols(w, p, False),
    "add_sheet": _x_add_sheet, "rename_sheet": _x_rename,
    "delete_sheet": _x_delete_sheet, "replace": _x_replace,
}


def _iter_shapes(shapes):
    for sh in shapes:
        if getattr(sh, "shape_type", None) == 6 and hasattr(sh, "shapes"):
            yield from _iter_shapes(sh.shapes)
        else:
            yield sh


def _shape_text(sh):
    if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
        return sh.text_frame.text
    if getattr(sh, "has_table", False) and sh.has_table:
        return "\n".join(" | ".join(c.text for c in r.cells)
                         for r in sh.table.rows)
    return ""


def _pptx_paragraphs(prs, notes=True):
    for slide in prs.slides:
        for sh in _iter_shapes(slide.shapes):
            if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
                yield from sh.text_frame.paragraphs
            if getattr(sh, "has_table", False) and sh.has_table:
                for r in sh.table.rows:
                    for c in r.cells:
                        yield from c.text_frame.paragraphs
        if notes and slide.has_notes_slide:
            yield from slide.notes_slide.notes_text_frame.paragraphs


def _pptx_info(prs, args):
    slides = []
    rng = _parse_range(args.get("range"), len(prs.slides), one_based=True)
    if isinstance(rng, str):
        return {"error": rng}
    for n, slide in enumerate(prs.slides, 1):
        if rng is not None and n - 1 not in rng:
            continue
        if rng is None and len(slides) >= 60:
            break
        shapes = []
        for sh in _iter_shapes(slide.shapes):
            txt = _shape_text(sh)
            kind = ("picture" if getattr(sh, "shape_type", None) == 13 else
                    "table" if getattr(sh, "has_table", False) and sh.has_table else
                    "chart" if getattr(sh, "has_chart", False) and sh.has_chart else
                    "text" if txt else "shape")
            if kind == "shape" and not txt:
                continue
            shapes.append({"id": sh.shape_id, "name": sh.name, "kind": kind,
                           **({"text": txt[:_CAP_TEXT]} if txt else {})})
        s = {"slide": n, "layout": slide.slide_layout.name, "shapes": shapes}
        if slide.has_notes_slide:
            nt = slide.notes_slide.notes_text_frame.text
            if nt.strip():
                s["notes"] = nt[:_CAP_TEXT]
        slides.append(s)
    return {"kind": "pptx", "slides_total": len(prs.slides),
            "size": f"{prs.slide_width}x{prs.slide_height} EMU",
            "layouts": [l.name for l in prs.slide_layouts], "slides": slides}


def _pptx_find(prs, find, match_case):
    hits = []
    for n, slide in enumerate(prs.slides, 1):
        for sh in _iter_shapes(slide.shapes):
            t = _shape_text(sh)
            if _count(t, find, match_case):
                hits.append({"slide": n, "shape": sh.shape_id,
                             "name": sh.name, "text": t[:200]})
        if slide.has_notes_slide and _count(
                slide.notes_slide.notes_text_frame.text, find, match_case):
            hits.append({"slide": n, "notes": True,
                         "text": slide.notes_slide.notes_text_frame.text[:200]})
    return hits


def _slide(prs, n):
    try:
        i = int(n)
        if i >= 1:
            return prs.slides[i - 1]
    except (TypeError, ValueError, IndexError):
        pass
    raise _StepError(f"slide {n} does not exist (1..{len(prs.slides)})")


def _shape(slide, ref):
    for sh in _iter_shapes(slide.shapes):
        if str(sh.shape_id) == str(ref) or sh.name == str(ref):
            return sh
    names = [f"{s.shape_id}:{s.name}" for s in _iter_shapes(slide.shapes)]
    raise _StepError(f"no shape '{ref}' on this slide; shapes: {', '.join(names)}")


def _tf_set_text(tf, text):
    lines = str(text or "").split("\n")
    pars = list(tf.paragraphs)
    first = pars[0]
    proto = copy.deepcopy(first._p)
    for extra in pars[1:]:
        extra._p.getparent().remove(extra._p)

    def fill(p, line):
        runs = list(p.runs)
        if runs:
            runs[0].text = line
            for r in runs[1:]:
                r._r.getparent().remove(r._r)
        else:
            p.add_run().text = line

    fill(first, lines[0])
    anchor = first._p
    from pptx.text.text import _Paragraph
    for line in lines[1:]:
        el = copy.deepcopy(proto)
        anchor.addnext(el)
        anchor = el
        fill(_Paragraph(el, first._parent), line)


def _p_replace(prs, p):
    find, repl = str(p.get("find") or ""), str(p.get("replace") or "")
    mc = p.get("match_case", True) is not False
    if not find:
        raise _StepError("find is empty")
    n = 0
    for par in _pptx_paragraphs(prs, notes=p.get("notes", True) is not False):
        n += _replace_in_runs(list(par.runs), find, repl, mc)
    if not n:
        raise _StepError(f"'{find}' does not occur in the deck")
    return f"{n} replaced"


def _p_set_text(prs, p):
    slide = _slide(prs, p.get("slide"))
    sh = _shape(slide, p.get("shape"))
    if not (getattr(sh, "has_text_frame", False) and sh.has_text_frame):
        raise _StepError(f"shape '{p.get('shape')}' holds no text")
    _tf_set_text(sh.text_frame, p.get("text"))
    return f"slide {p.get('slide')} shape {sh.shape_id} «{sh.name}» rewritten"


def _layout(prs, ref):
    lays = list(prs.slide_layouts)
    if ref is None or ref == "":
        for l in lays:
            if l.name.lower() in ("title and content", "título y objetos"):
                return l
        return lays[1] if len(lays) > 1 else lays[0]
    if isinstance(ref, int) or str(ref).isdigit():
        i = int(ref)
        if 0 <= i < len(lays):
            return lays[i]
    for l in lays:
        if l.name.lower() == str(ref).lower():
            return l
    raise _StepError(f"layout '{ref}' does not exist; layouts: "
                     f"{', '.join(f'{i}:{l.name}' for i, l in enumerate(lays))}")


def _move_slide(prs, old_i, new_i):
    lst = prs.slides._sldIdLst
    el = list(lst)[old_i]
    lst.remove(el)
    lst.insert(new_i, el)


def _p_add_slide(prs, p):
    slide = prs.slides.add_slide(_layout(prs, p.get("layout")))
    done = []
    if p.get("title") is not None and slide.shapes.title is not None:
        slide.shapes.title.text = str(p["title"])
        done.append("title")
    body = p.get("body")
    if body is not None:
        ph = next((s for s in slide.placeholders
                   if s.placeholder_format.idx != 0 and s.has_text_frame), None)
        if ph is None:
            raise _StepError("this layout has no body placeholder; pick "
                             "another layout or use set_text after")
        _tf_set_text(ph.text_frame, "\n".join(map(str, body))
                     if isinstance(body, list) else str(body))
        done.append("body")
    n = len(prs.slides)
    if p.get("position") is not None:
        pos = max(1, min(int(p["position"]), n))
        _move_slide(prs, n - 1, pos - 1)
        n = pos
    return f"slide {n} added ({slide.slide_layout.name}{': ' + ', '.join(done) if done else ''})"


def _p_delete_slide(prs, p):
    slide = _slide(prs, p.get("slide"))
    lst = prs.slides._sldIdLst
    for el in list(lst):
        if prs.slides.get(int(el.get("id"))) is slide:
            prs.part.drop_rel(el.rId)
            lst.remove(el)
            return f"slide {p.get('slide')} deleted"
    raise _StepError("slide not found")


def _p_move_slide(prs, p):
    _slide(prs, p.get("slide"))
    to = int(p.get("to") or 0)
    if not 1 <= to <= len(prs.slides):
        raise _StepError(f"to must be 1..{len(prs.slides)}")
    _move_slide(prs, int(p["slide"]) - 1, to - 1)
    return f"slide {p['slide']} → position {to}"


def _p_notes(prs, p):
    slide = _slide(prs, p.get("slide"))
    slide.notes_slide.notes_text_frame.text = str(p.get("text") or "")
    return f"slide {p.get('slide')}: notes set"


_P_VERBS = {
    "replace": _p_replace, "set_text": _p_set_text, "add_slide": _p_add_slide,
    "delete_slide": _p_delete_slide, "move_slide": _p_move_slide,
    "notes": _p_notes,
}

VERBS = {"docx": _W_VERBS, "xlsx": _X_VERBS, "pptx": _P_VERBS}


def _parse_range(spec, n, one_based=False):
    if spec in (None, ""):
        return None
    out = set()
    off = 1 if one_based else 0
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        try:
            a, b = (int(m.group(1)), int(m.group(2))) if m else (int(part),) * 2
        except ValueError:
            return f"range '{spec}' is not like '3-10,14'"
        out.update(i - off for i in range(a, b + 1) if 0 <= i - off < n)
    return out


_VIEW_CSS = """<style>
 html,body{margin:0;background:#0f172a;}
 body{padding:18px;font-family:Calibri,Carlito,'Segoe UI',Arial,sans-serif;}
 .head{color:#94a3b8;font:12px sans-serif;margin:0 0 12px;}
 .page{background:#fff;color:#111;max-width:820px;margin:0 auto 18px;
   padding:56px 64px;box-shadow:0 4px 24px rgba(0,0,0,.4);border-radius:3px;
   line-height:1.45;font-size:15px;}
 .page table{border-collapse:collapse;margin:10px 0;}
 .page td{border:1px solid #999;padding:4px 8px;vertical-align:top;}
 .page td p,.page td h1,.page td h2,.page td h3{margin:0;}
 .page img{max-width:100%;}
 .sheet{background:#fff;color:#111;margin:0 0 18px;overflow:auto;
   border-radius:3px;box-shadow:0 4px 24px rgba(0,0,0,.4);}
 .sheet h3{margin:0;padding:8px 12px;background:#e2e8f0;font:600 13px sans-serif;}
 .sheet table{border-collapse:collapse;font-size:12px;}
 .sheet td,.sheet th{border:1px solid #d4d4d8;padding:2px 6px;white-space:nowrap;}
 .sheet th{background:#f1f5f9;color:#64748b;font-weight:500;}
 .sheet td.f{color:#64748b;font-style:italic;}
 .slide{position:relative;background:#fff;color:#111;margin:0 auto 18px;
   box-shadow:0 4px 24px rgba(0,0,0,.4);overflow:hidden;border-radius:3px;}
 .slide .sh{position:absolute;overflow:hidden;font-size:14px;line-height:1.25;}
 .slide .n{position:absolute;right:8px;bottom:6px;color:#94a3b8;font:11px sans-serif;}
 .notes{max-width:960px;margin:-10px auto 22px;color:#cbd5e1;font:12px sans-serif;
   white-space:pre-wrap;}
</style>"""


def _esc(s):
    return _h.escape(str(s if s is not None else ""))


def _run_html(r):
    t = _esc(r.text).replace("\n", "<br>")
    try:
        if r.bold:
            t = f"<b>{t}</b>"
        if r.italic:
            t = f"<i>{t}</i>"
        if r.underline:
            t = f"<u>{t}</u>"
    except Exception:
        pass
    return t


def _docx_view(doc, media_dir):
    from docx.oxml.ns import qn
    images = {}
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
                name = os.path.basename(rel.target_part.partname)
                path = os.path.join(media_dir, name)
                with open(path, "wb") as f:
                    f.write(blob)
                images[rel.rId] = path
            except Exception:
                pass

    def par_html(par):
        bits = []
        for r in _docx_runs(par):
            bits.append(_run_html(r))
            for blip in r._r.iter(qn("a:blip")):
                rid = blip.get(qn("r:embed"))
                if rid in images:
                    bits.append(f'<img src="{_esc(images[rid])}">')
        inner = "".join(bits) or "&nbsp;"
        st = (par.style.name if par.style is not None else "").lower()
        m = re.match(r"(heading|título|titulo)\s*(\d)", st)
        if st in ("title", "título", "titulo"):
            return f"<h1>{inner}</h1>"
        if m:
            lvl = min(int(m.group(2)) + 1, 6)
            return f"<h{lvl}>{inner}</h{lvl}>"
        if "list" in st or "lista" in st:
            return f"<p style='margin:2px 0 2px 24px'>• {inner}</p>"
        al = {1: "center", 2: "right", 3: "justify"}.get(
            int(par.alignment) if par.alignment is not None else 0, "")
        return f"<p{f' style=text-align:{al}' if al else ''}>{inner}</p>"

    out = []
    for k, b in _docx_blocks(doc):
        if k == "p":
            out.append(par_html(b))
        else:
            rows = "".join("<tr>" + "".join(
                f"<td>{''.join(par_html(p) for p in c.paragraphs)}</td>"
                for c in r.cells) + "</tr>" for r in b.rows)
            out.append(f"<table>{rows}</table>")
    return f'<div class="page">{"".join(out)}</div>'


def _xlsx_view(path):
    import openpyxl
    from openpyxl.utils import get_column_letter
    wb = openpyxl.load_workbook(path, data_only=True)
    wbf = _open(path, "xlsx")
    out = []
    for ws in wb.worksheets:
        wsf = wbf[ws.title]
        nr, nc = min(ws.max_row, _VIEW_ROWS), min(ws.max_column, _VIEW_COLS)
        spans, hidden = {}, set()
        for m in ws.merged_cells.ranges:
            if m.min_row > nr or m.min_col > nc:
                continue
            spans[(m.min_row, m.min_col)] = (m.max_row - m.min_row + 1,
                                             m.max_col - m.min_col + 1)
            for r in range(m.min_row, m.max_row + 1):
                for c in range(m.min_col, m.max_col + 1):
                    if (r, c) != (m.min_row, m.min_col):
                        hidden.add((r, c))
        head = "<tr><th></th>" + "".join(
            f"<th>{get_column_letter(c)}</th>" for c in range(1, nc + 1)) + "</tr>"
        rows = []
        for r in range(1, nr + 1):
            tds = [f"<th>{r}</th>"]
            for c in range(1, nc + 1):
                if (r, c) in hidden:
                    continue
                cell = ws.cell(row=r, column=c)
                v = cell.value
                fv = wsf.cell(row=r, column=c).value
                style = []
                try:
                    if cell.font is not None and cell.font.b:
                        style.append("font-weight:700")
                    fg = cell.fill.fgColor.rgb if cell.fill and cell.fill.fill_type == "solid" else None
                    if isinstance(fg, str) and len(fg) == 8 and fg != "00000000":
                        style.append(f"background:#{fg[2:]}")
                except Exception:
                    pass
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    style.append("text-align:right")
                sp = spans.get((r, c))
                attrs = (f' rowspan="{sp[0]}" colspan="{sp[1]}"' if sp else "")
                if isinstance(fv, str) and fv.startswith("="):
                    attrs += f' title="{_esc(fv)}"'
                    if v is None:
                        v = fv
                        attrs += ' class="f"'
                if style:
                    attrs += f' style="{";".join(style)}"'
                tds.append(f"<td{attrs}>{_esc(_xl_value(v)) if v is not None else ''}</td>")
            rows.append("<tr>" + "".join(tds) + "</tr>")
        cut = ""
        if ws.max_row > nr or ws.max_column > nc:
            cut = f" — {ws.dimensions}, shown {get_column_letter(nc)}{nr}"
        out.append(f'<div class="sheet"><h3>{_esc(ws.title)}{_esc(cut)}</h3>'
                   f"<table>{head}{''.join(rows)}</table></div>")
    return "".join(out)


def _pptx_view(prs, media_dir, width_px=960):
    sw, sh_ = int(prs.slide_width or 12192000), int(prs.slide_height or 6858000)
    k = width_px / sw
    out = []
    for n, slide in enumerate(prs.slides, 1):
        boxes = []
        for sh in _iter_shapes(slide.shapes):
            try:
                x, y = int(sh.left or 0) * k, int(sh.top or 0) * k
                w, h = int(sh.width or 0) * k, int(sh.height or 0) * k
            except Exception:
                continue
            pos = f"left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px"
            if getattr(sh, "shape_type", None) == 13:
                try:
                    name = f"s{n}-{sh.shape_id}.{sh.image.ext}"
                    path = os.path.join(media_dir, name)
                    with open(path, "wb") as f:
                        f.write(sh.image.blob)
                    boxes.append(f'<img class="sh" style="{pos};object-fit:contain" '
                                 f'src="{_esc(path)}">')
                except Exception:
                    pass
                continue
            if getattr(sh, "has_table", False) and sh.has_table:
                rows = "".join("<tr>" + "".join(
                    f"<td style='border:1px solid #999;padding:2px 4px'>{_esc(c.text)}</td>"
                    for c in r.cells) + "</tr>" for r in sh.table.rows)
                boxes.append(f'<div class="sh" style="{pos}"><table style="'
                             f'border-collapse:collapse;font-size:12px">{rows}</table></div>')
                continue
            if getattr(sh, "has_chart", False) and sh.has_chart:
                boxes.append(f'<div class="sh" style="{pos};border:1px dashed #94a3b8;'
                             f'color:#64748b;display:flex;align-items:center;'
                             f'justify-content:center">chart</div>')
                continue
            if not (getattr(sh, "has_text_frame", False) and sh.has_text_frame):
                continue
            ph = (int(sh.placeholder_format.type)
                  if getattr(sh, "is_placeholder", False) else 0)
            bullet = "• " if ph in (2, 7) else ""
            pars = []
            for p in sh.text_frame.paragraphs:
                size = None
                for r in p.runs:
                    if r.font.size:
                        size = r.font.size.pt
                        break
                fs = f"font-size:{size * 12700 * k:.0f}px;" if size else ""
                inner = "".join(_run_html(r) for r in p.runs)
                ind = f"margin-left:{18 * p.level}px;" if p.level else ""
                pars.append(f"<div style='{fs}{ind}'>{bullet + inner if inner else '&nbsp;'}</div>")
            extra = ("font-size:28px;font-weight:600;" if ph in (1, 3) else "")
            if ph in (3, 4):
                extra += "text-align:center;display:flex;flex-direction:column;justify-content:center;"
            boxes.append(f'<div class="sh" style="{pos};{extra}">{"".join(pars)}</div>')
        out.append(f'<div class="slide" style="width:{width_px}px;height:'
                   f'{sh_ * k:.0f}px">{"".join(boxes)}<div class="n">{n}</div></div>')
        if slide.has_notes_slide:
            nt = slide.notes_slide.notes_text_frame.text.strip()
            if nt:
                out.append(f'<div class="notes" style="max-width:{width_px}px">'
                           f'{_esc(nt)}</div>')
    return "".join(out)


def build_view(path, kind):
    tag = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    vdir = os.path.join(_base_dir(), "media", "office", tag)
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(vdir, exist_ok=True)
    if kind == "docx":
        body = _docx_view(_open(path, kind), vdir)
    elif kind == "xlsx":
        body = _xlsx_view(path)
    else:
        body = _pptx_view(_open(path, kind), vdir)
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'>" + _VIEW_CSS
            + f"</head><body><div class='head'>{_esc(os.path.basename(path))}"
            "</div>" + body + "</body></html>")
    out = os.path.join(vdir, "view.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out, page


def plain_text(path, cap=10000):
    p, k = _check_source(path)
    if p is None:
        return f"({k})"
    if _missing_lib(k):
        return f"({_missing_lib(k)})"
    try:
        if k == "docx":
            doc = _open(p, k)
            out = []
            for kind, b in _docx_blocks(doc):
                if kind == "p":
                    out.append(b.text)
                else:
                    out.extend(" | ".join(c.text for c in r.cells) for r in b.rows)
            text = "\n".join(out)
        elif k == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
            out = []
            for ws in wb.worksheets:
                out.append(f"## {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    if any(v is not None for v in row):
                        out.append("\t".join("" if v is None else str(_xl_value(v))
                                             for v in row))
                    if sum(len(x) for x in out) > cap:
                        break
            text = "\n".join(out)
        else:
            prs = _open(p, k)
            out = []
            for n, slide in enumerate(prs.slides, 1):
                out.append(f"## slide {n}")
                out.extend(t for t in (_shape_text(s) for s in _iter_shapes(slide.shapes)) if t)
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                    out.append("notes: " + slide.notes_slide.notes_text_frame.text)
            text = "\n".join(out)
    except Exception as e:
        return f"(could not read {os.path.basename(p)}: {e})"
    return text[:cap]


def tool_office_info(args):
    p, k = _check_source(args.get("source"))
    if p is None:
        return {"error": k}
    if _missing_lib(k):
        return {"error": _missing_lib(k)}
    try:
        find = str(args.get("find") or "")
        mc = args.get("match_case", True) is not False
        if k == "xlsx":
            out = _xlsx_info(p, args)
            if find and "error" not in out:
                out["found"] = _xlsx_find(p, find, mc)
        else:
            doc = _open(p, k)
            out = _docx_info(doc, args) if k == "docx" else _pptx_info(doc, args)
            if find and "error" not in out:
                out["found"] = (_docx_find(doc, find, mc) if k == "docx"
                                else _pptx_find(doc, find, mc))
    except Exception as e:
        return {"error": f"could not read {os.path.basename(p)}: {e}"}
    if "error" in out:
        return out
    out["source"] = p
    if args.get("view"):
        try:
            vpath, _page = build_view(p, k)
            out["view"] = vpath
            try:
                import fed_agent
                fed_agent.tool_progress("office.view", path=vpath,
                                        name=os.path.basename(p))
            except Exception:
                pass
            out["view_note"] = ("Painted on the canvas when there is a GUI; "
                                "content is faithful, layout approximate.")
        except Exception as e:
            out["view"] = {"error": f"could not render the view: {e}"}
    return out


def tool_office(args):
    p, k = _check_source(args.get("source"))
    if p is None:
        return {"error": k}
    if _missing_lib(k):
        return {"error": _missing_lib(k)}
    output = os.path.abspath(os.path.expanduser(str(args.get("output") or "").strip())) \
        if args.get("output") else ""
    if not output:
        return {"error": "output path required (it may be the source itself)"}
    if kind_of(output) != k:
        return {"error": f"output must keep the {k} kind "
                         f"({os.path.splitext(p)[1]}): got '{os.path.basename(output)}'"}
    steps = args.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return {"error": "steps must be a non-empty list of {verb: {...}}"}
    verbs = VERBS[k]
    try:
        obj = _open(p, k)
    except Exception as e:
        return {"error": f"could not open {os.path.basename(p)}: {e}"}
    narrative = []
    for si, step in enumerate(steps, 1):
        if not isinstance(step, dict) or len(step) != 1:
            return {"error": f"step {si}: each step is one {{verb: params}}",
                    "narrative": narrative, "written": False}
        verb, params = next(iter(step.items()))
        fn = verbs.get(verb)
        if fn is None:
            return {"error": f"step {si}: '{verb}' is not a {k} verb — "
                             f"available: {', '.join(sorted(verbs))}",
                    "narrative": narrative, "written": False}
        try:
            note = fn(obj, params if isinstance(params, dict) else {})
        except _StepError as e:
            return {"error": f"step {si} ({verb}): {e}", "narrative": narrative,
                    "written": False}
        except Exception as e:
            return {"error": f"step {si} ({verb}) blew up: {e}",
                    "narrative": narrative, "written": False}
        narrative.append(f"{si}. {verb}: {note}")

    out_dir = os.path.dirname(output) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hoplita-", suffix=os.path.splitext(output)[1],
                               dir=out_dir)
    os.close(fd)
    try:
        obj.save(tmp)
        fd0, tmp0 = tempfile.mkstemp(prefix=".hoplita-", suffix=os.path.splitext(output)[1],
                                     dir=out_dir)
        os.close(fd0)
        try:
            _open(p, k).save(tmp0)
            lost = _lost(_parts_by_kind(p), _parts_by_kind(tmp0))
        finally:
            os.unlink(tmp0)
        in_place = os.path.abspath(output) == p
        if lost and in_place and not args.get("accept_loss"):
            os.unlink(tmp)
            return {"error": "NOT written: saving would drop parts of the file "
                             "that this library cannot carry",
                    "lost": {kk: f"{a} → {b}" for kk, (a, b) in lost.items()},
                    "narrative": narrative, "written": False,
                    "hint": "Write to a different output to keep the original "
                            "intact, or pass accept_loss=true to overwrite "
                            "anyway."}
        backup = ""
        if os.path.exists(output):
            bdir = os.path.join(_base_dir(), "office-backups")
            os.makedirs(bdir, exist_ok=True)
            backup = os.path.join(bdir, datetime.datetime.now().strftime(
                "%Y%m%d-%H%M%S-") + os.path.basename(output))
            shutil.copy2(output, backup)
        os.replace(tmp, output)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"error": f"could not write {output}: {e}",
                "narrative": narrative, "written": False}
    res = {"written": output, "narrative": narrative}
    if backup:
        res["backup"] = backup
    if lost:
        res["lost"] = {kk: f"{a} → {b}" for kk, (a, b) in lost.items()}
        res["warning"] = ("these parts of the source are NOT in the output: "
                          "the library could not carry them")
    return res
