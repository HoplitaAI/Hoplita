
import copy
import io
import json
import os
import re



def _need_pypdf():
    try:
        import PyPDF2
        return PyPDF2, None
    except Exception as e:
        return None, f"PyPDF2 unavailable: {e} — pip install PyPDF2"


def _need_fpdf():
    try:
        import fpdf
        return fpdf, None
    except Exception as e:
        return None, f"fpdf2 unavailable: {e} — pip install fpdf2"



def _open_reader(source, password=""):
    PDF, err = _need_pypdf()
    if err:
        return None, err
    p = os.path.expanduser(str(source or ""))
    if not os.path.isfile(p):
        return None, f"not a file: {p}"
    try:
        reader = PDF.PdfReader(p)
        if reader.is_encrypted:
            ok = reader.decrypt(password or "")
            if not ok:
                return None, ("encrypted PDF: wrong or missing password "
                              "(pass password=...)")
        _ = len(reader.pages)
        return reader, None
    except Exception as e:
        return None, f"cannot open {os.path.basename(p)}: {e}"


def plain_text(source, limit=10_000):
    import subprocess
    p = os.path.expanduser(str(source or ""))
    try:
        r = subprocess.run(["pdftotext", p, "-"], capture_output=True, text=True,
                           timeout=30, **__import__("hoplita_paths").no_console())
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout[:limit], None
    except (OSError, subprocess.SubprocessError):
        pass
    reader, err = _open_reader(p)
    if err:
        return "", err
    out = []
    size = 0
    for page in reader.pages:
        try:
            chunk = page.extract_text() or ""
        except Exception:
            chunk = ""
        out.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    text = "\n".join(out).strip()
    if not text:
        return "", "no text layer (a scanned PDF: pdf_info can render its pages to look at)"
    return text[:limit], None


def _parse_pages(spec, total):
    if spec in (None, "", "all"):
        return list(range(total)), None
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        try:
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a > b:
                    a, b = b, a
                out.extend(range(a - 1, b))
            else:
                out.append(int(part) - 1)
        except ValueError:
            return None, f"bad page spec: {part!r}"
    bad = [i + 1 for i in out if i < 0 or i >= total]
    if bad:
        return None, f"page(s) {bad} out of range (document has {total})"
    return out, None


def _page_band_texts(reader, band="bottom", frac=0.16, max_pages=None):
    out = []
    n = len(reader.pages)
    for i in range(min(n, max_pages or n)):
        page = reader.pages[i]
        try:
            h = float(page.mediabox.height)
            x1 = float(page.mediabox.width)
        except Exception:
            h, x1 = 842.0, 595.0
        hits = []

        def visitor(text, cm, tm, font_dict, font_size,
                    _h=h, _hits=hits):
            t = (text or "").strip()
            if not t:
                return
            y = tm[5] if tm else 0
            if band == "bottom" and y <= _h * frac:
                _hits.append((t, tm[4] if tm else 0, y))
            elif band == "top" and y >= _h * (1 - frac):
                _hits.append((t, tm[4] if tm else 0, y))

        try:
            page.extract_text(visitor_text=visitor)
        except Exception:
            pass
        out.append((i, hits, x1))
    return out


def _auto_layout(reader):
    pages = reader.pages
    n = len(pages)
    if n == 0:
        return None, "empty document"
    landscape = 0
    for p in pages:
        try:
            if float(p.mediabox.width) > float(p.mediabox.height) * 1.15:
                landscape += 1
        except Exception:
            pass
    two_up = landscape >= max(1, n // 2 + 1)

    bands = _page_band_texts(reader, "bottom")
    if not any(h for _, h, _ in bands):
        bands = _page_band_texts(reader, "top")
    folios = []
    for i, hits, width in bands:
        for t, x, _y in hits:
            for tok in re.findall(r"\b(\d{1,4})\b", t):
                half = ("left" if x < width / 2 else "right") if two_up \
                    else "full"
                folios.append((i, half, int(tok)))
    if not folios:
        return None, ("no readable folio numbers (pure scan or unnumbered) "
                      "— pass the mapping yourself (offset=..., two_up=...) "
                      "or look at a page with pdf_info render")
    diffs = []
    for i, half, num in folios:
        if two_up:
            slot_lf = i * 2 + (0 if half == "left" else 1)
            slot_rf = i * 2 + (0 if half == "right" else 1)
            diffs.append(("left-first", num - slot_lf))
            diffs.append(("right-first", num - slot_rf))
        else:
            diffs.append(("full", num - i))
    from collections import Counter
    tally = Counter()
    for order, d in diffs:
        tally[(order, d)] += 1
    (win_order, win_diff), votes = tally.most_common(1)[0]
    consistent = votes / max(1, len(folios))
    if consistent < 0.5:
        return None, (f"printed numbers found but no consistent progression "
                      f"(best fit only {votes}/{len(folios)}) — pass the "
                      "mapping yourself")
    order = win_order if two_up else None
    mapping = {"two_up": two_up, "order": order,
               "slot_shift": win_diff, "evidence": votes,
               "folios_read": len(folios)}
    why = (f"{'two-up ' + order if two_up else 'single-page'} · printed "
           f"folio fits slot{win_diff:+d} on {votes}/{len(folios)} readings")
    return mapping, why


def _logical_to_slots(logical_pages, mapping):
    return [lp - mapping["slot_shift"] for lp in logical_pages]



def tool_pdf_info(args):
    src = args.get("source", "")
    reader, err = _open_reader(src, args.get("password", ""))
    if err:
        return {"error": err}
    out = {"source": os.path.expanduser(src), "pages": len(reader.pages)}
    try:
        out["sizes"] = sorted({(round(float(p.mediabox.width)),
                                round(float(p.mediabox.height)))
                               for p in reader.pages})
        out["sizes"] = [f"{w}x{h}" for w, h in out["sizes"]][:6]
    except Exception:
        pass
    try:
        md = reader.metadata or {}
        out["meta"] = {k.lstrip("/"): str(v) for k, v in md.items()
                       if v is not None}
    except Exception:
        pass
    try:
        fields = reader.get_fields() or {}
        out["form_fields"] = {k: (v.get("/V", "") if hasattr(v, "get") else "")
                              for k, v in fields.items()} or None
        if out["form_fields"] is None:
            del out["form_fields"]
    except Exception:
        pass
    try:
        ol = reader.outline
        out["toc"] = _flatten_outline(ol)[:60] if ol else []
    except Exception:
        out["toc"] = []
    mapping, why = _auto_layout(reader)
    out["layout"] = ({"detected": mapping, "why": why} if mapping
                     else {"detected": None, "why": why})
    want = args.get("text", "")
    if want:
        pages, perr = _parse_pages(args.get("pages", "all"),
                                   len(reader.pages))
        if perr:
            return {"error": perr}
        texts = {}
        if want in ("bottom", "top"):
            for i, hits, _w in _page_band_texts(reader, want):
                if i in pages:
                    texts[str(i + 1)] = " ".join(t for t, _x, _y in hits)[:300]
        else:
            for i in pages[:40]:
                try:
                    texts[str(i + 1)] = (reader.pages[i].extract_text()
                                         or "")[:2000]
                except Exception:
                    texts[str(i + 1)] = "(no text layer)"
        out["text"] = texts
    if args.get("render"):
        png, rerr = _render_page(os.path.expanduser(src),
                                 int(args["render"]) - 1,
                                 int(args.get("dpi", 120)),
                                 args.get("password", ""))
        if rerr:
            out["render"] = {"error": rerr}
        else:
            out["render"] = png
    return out


def _flatten_outline(items, depth=0, acc=None):
    acc = acc if acc is not None else []
    for it in items:
        if isinstance(it, list):
            _flatten_outline(it, depth + 1, acc)
        else:
            try:
                acc.append(("  " * depth) + str(it.title))
            except Exception:
                pass
    return acc


def _render_page(path, page_idx, dpi, password=""):
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtPdf import QPdfDocument
        from PyQt6.QtCore import QSize
    except Exception as e:
        return None, f"render unavailable (QtPdf not importable: {e})"
    try:
        if QGuiApplication.instance() is None:
            _render_page._app = QGuiApplication([])
        doc = QPdfDocument(None)
        doc.load(path)
        if password and doc.status() == QPdfDocument.Status.Error:
            doc.setPassword(password)
            doc.load(path)
        if doc.status() != QPdfDocument.Status.Ready:
            return None, f"QtPdf could not load {os.path.basename(path)}"
        if page_idx < 0 or page_idx >= doc.pageCount():
            return None, f"page {page_idx + 1} out of range ({doc.pageCount()})"
        sz = doc.pagePointSize(page_idx)
        w = max(1, int(sz.width() * dpi / 72.0))
        h = max(1, int(sz.height() * dpi / 72.0))
        img = doc.render(page_idx, QSize(w, h))
        outp = os.path.join(
            os.path.dirname(path) or ".",
            f"{os.path.splitext(os.path.basename(path))[0]}"
            f"_p{page_idx + 1}_{dpi}dpi.png")
        if not img.save(outp, "PNG"):
            return None, "QtPdf rendered but PNG save failed"
        return {"png": outp, "width": w, "height": h,
                "note": "open it, or paste it into the chat for a vision "
                        "model to read the printed folio"}, None
    except Exception as e:
        return None, f"render failed: {e}"



def tool_pdf(args):
    PDF, err = _need_pypdf()
    if err:
        return {"error": err}
    src = args.get("source", "")
    output = os.path.expanduser(args.get("output", "") or "")
    if not output:
        return {"error": "output path required (the clay must land somewhere)"}
    reader, err = _open_reader(src, args.get("password", ""))
    if err:
        return {"error": err}
    steps = args.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return {"error": "steps must be a non-empty list of {verb: {...}}"}

    work = [copy.deepcopy(p) for p in reader.pages]
    narrative = []
    mapping_cache = None
    encrypt_at_end = None

    for si, step in enumerate(steps, 1):
        if not isinstance(step, dict) or len(step) != 1:
            return {"error": f"step {si}: each step is one {{verb: params}}"}
        verb, params = next(iter(step.items()))
        params = params if isinstance(params, dict) else {}
        fn = _VERBS.get(verb)
        if fn is None:
            return {"error": f"step {si}: unknown verb '{verb}' — "
                             f"available: {', '.join(sorted(_VERBS))}"}
        try:
            work, note, mapping_cache, encrypt_at_end = fn(
                work, params, reader, mapping_cache, encrypt_at_end)
        except _StepError as e:
            return {"error": f"step {si} ({verb}): {e}",
                    "narrative": narrative}
        except Exception as e:
            return {"error": f"step {si} ({verb}) blew up: {e}",
                    "narrative": narrative}
        narrative.append(f"{si}. {verb}: {note}")

    if not work:
        return {"error": "pipeline ended with ZERO pages — refusing to "
                         "write an empty PDF", "narrative": narrative}
    writer = PDF.PdfWriter()
    for p in work:
        writer.add_page(p)
    try:
        from PyPDF2.generic import (ArrayObject, BooleanObject,
                                    DictionaryObject, NameObject)
        _frefs = []
        for p in writer.pages:
            for a in (p.get("/Annots") or []):
                o = a.get_object()
                if o.get("/Subtype") == "/Widget" and o.get("/T"):
                    _frefs.append(a)
        if _frefs:
            writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
                NameObject("/Fields"): ArrayObject(_frefs),
                NameObject("/NeedAppearances"): BooleanObject(True)})
    except Exception:
        pass
    md = args.get("metadata")
    if isinstance(md, dict) and md:
        writer.add_metadata({("/" + k.lstrip("/")): str(v)
                             for k, v in md.items()})
        narrative.append(f"{len(steps) + 1}. metadata: "
                         f"{', '.join(md)} written")
    if encrypt_at_end:
        writer.encrypt(encrypt_at_end.get("password", ""),
                       encrypt_at_end.get("owner_password") or None)
        narrative.append(f"{len(steps) + 1}. encrypt: sealed with the given "
                         "password")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "wb") as f:
        writer.write(f)
    return {"output": output, "pages": len(work),
            "size": os.path.getsize(output), "narrative": narrative}


class _StepError(Exception):
    pass



def _v_split_two_up(work, params, reader, mapping, enc):
    order = params.get("order", "auto")
    if order == "auto":
        m, why = _auto_layout(reader)
        if m and m.get("two_up"):
            order = m["order"] or "left-first"
            mapping = m
        elif m and not m.get("two_up"):
            raise _StepError(f"auto says this is NOT a two-up scan ({why}) "
                             "— pass order explicitly to force the split")
        else:
            raise _StepError(f"cannot auto-detect order: {why}")
    halves = []
    for p in work:
        left = copy.deepcopy(p)
        right = copy.deepcopy(p)
        mb = p.mediabox
        midx = (float(mb.left) + float(mb.right)) / 2.0
        for target, side in ((left, "left"), (right, "right")):
            box = target.mediabox
            if side == "left":
                box.upper_right = (midx, float(mb.top))
            else:
                box.lower_left = (midx, float(mb.bottom))
            target.cropbox = box
        halves.extend([left, right] if order == "left-first"
                      else [right, left])
    return halves, (f"{len(work)} sheet(s) → {len(halves)} half-pages "
                    f"({order})"), mapping, enc


def _v_select(work, params, reader, mapping, enc):
    numbering = params.get("numbering", "physical")
    spec = params.get("pages", "")
    if numbering == "physical":
        idx, err = _parse_pages(spec, len(work))
        if err:
            raise _StepError(err)
        return ([work[i] for i in idx],
                f"kept {len(idx)} page(s) [{spec}] (physical)",
                mapping, enc)
    logical, err = _parse_pages(spec, 10 ** 6)
    if err:
        raise _StepError(err)
    logical = [i + 1 for i in logical]
    if params.get("auto") or "offset" not in params:
        if mapping is None:
            mapping, why = _auto_layout(reader)
            if mapping is None:
                raise _StepError(f"printed numbering needs a mapping and "
                                 f"auto could not build one: {why}")
        slots = _logical_to_slots(logical, mapping)
        how = (f"auto mapping ({mapping['evidence']}/"
               f"{mapping['folios_read']} folios agree)")
    else:
        off = int(params.get("offset", 0))
        slots = [lp - 1 + off for lp in logical]
        how = f"given offset {off:+d}"
    bad = [logical[j] for j, s in enumerate(slots)
           if s < 0 or s >= len(work)]
    if bad:
        raise _StepError(f"printed page(s) {bad} fall outside the document "
                         f"({len(work)} slots after previous steps) — check "
                         "the mapping/offset")
    return ([work[s] for s in slots],
            f"kept printed page(s) {logical} → slots "
            f"{[s + 1 for s in slots]} ({how})", mapping, enc)


def _v_merge(work, params, reader, mapping, enc):
    other, err = _open_reader(params.get("source", ""),
                              params.get("password", ""))
    if err:
        raise _StepError(err)
    incoming = [copy.deepcopy(p) for p in other.pages]
    pos = params.get("position", "end")
    if pos == "end":
        work = work + incoming
    elif pos == "start":
        work = incoming + work
    else:
        at = max(0, min(len(work), int(pos)))
        work = work[:at] + incoming + work[at:]
    return (work, f"merged {len(incoming)} page(s) from "
            f"{os.path.basename(str(params.get('source')))} at {pos}",
            mapping, enc)


def _v_rotate(work, params, reader, mapping, enc):
    deg = int(params.get("degrees", 90))
    if deg % 90:
        raise _StepError("degrees must be a multiple of 90")
    idx, err = _parse_pages(params.get("pages", "all"), len(work))
    if err:
        raise _StepError(err)
    for i in idx:
        work[i].rotate(deg)
    return work, f"rotated {len(idx)} page(s) by {deg}°", mapping, enc


def _v_crop(work, params, reader, mapping, enc):
    idx, err = _parse_pages(params.get("pages", "all"), len(work))
    if err:
        raise _StepError(err)
    box = params.get("box")
    half = params.get("half")
    if not box and half not in ("left", "right", "top", "bottom"):
        raise _StepError("give box=[x0,y0,x1,y1] (points) or "
                         "half=left|right|top|bottom")
    for i in idx:
        p = work[i]
        mb = p.mediabox
        if box:
            x0, y0, x1, y1 = [float(v) for v in box]
        else:
            L, B = float(mb.left), float(mb.bottom)
            R, T = float(mb.right), float(mb.top)
            mx, my = (L + R) / 2, (B + T) / 2
            x0, y0, x1, y1 = {"left": (L, B, mx, T),
                              "right": (mx, B, R, T),
                              "top": (L, my, R, T),
                              "bottom": (L, B, R, my)}[half]
        p.mediabox.lower_left = (x0, y0)
        p.mediabox.upper_right = (x1, y1)
        p.cropbox = p.mediabox
    return (work, f"cropped {len(idx)} page(s) to "
            f"{'box ' + str(box) if box else half + ' half'}", mapping, enc)


def _v_scale(work, params, reader, mapping, enc):
    f = float(params.get("factor", 1.0))
    if not (0.05 <= f <= 20):
        raise _StepError("factor out of sane range")
    idx, err = _parse_pages(params.get("pages", "all"), len(work))
    if err:
        raise _StepError(err)
    for i in idx:
        work[i].scale_by(f)
    return work, f"scaled {len(idx)} page(s) ×{f}", mapping, enc


def _v_n_up(work, params, reader, mapping, enc):
    PDF, _ = _need_pypdf()
    per = int(params.get("per_page", 2))
    if per != 2:
        raise _StepError("only per_page=2 for now (the two-up composer)")
    out = []
    it = iter(work)
    for a in it:
        b = next(it, None)
        aw = float(a.mediabox.width)
        ah = float(a.mediabox.height)
        sheet = PDF.PageObject.create_blank_page(width=aw * 2, height=ah)
        a.add_transformation(PDF.Transformation().translate(
            tx=-float(a.mediabox.left), ty=-float(a.mediabox.bottom)))
        sheet.merge_page(a)
        if b is not None:
            b.add_transformation(PDF.Transformation().translate(
                tx=aw - float(b.mediabox.left),
                ty=-float(b.mediabox.bottom)))
            sheet.merge_page(b)
        out.append(sheet)
    return out, f"{len(work)} page(s) → {len(out)} sheet(s) 2-up", \
        mapping, enc


def _seal_pdf(text, w, h, size, angle, color, opacity):
    FP, err = _need_fpdf()
    if err:
        raise _StepError(err)
    doc = FP.FPDF(unit="pt", format=(w, h))
    doc.add_page()
    doc.set_font("Helvetica", "B", size)
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except Exception:
        r, g, b = 180, 180, 180
    doc.set_text_color(r, g, b)
    tw = doc.get_string_width(text)
    x, y = (w - tw) / 2, h / 2
    try:
        with doc.local_context(fill_opacity=opacity, stroke_opacity=opacity):
            with doc.rotation(angle, x=w / 2, y=h / 2):
                doc.text(x, y, text)
    except Exception:
        with doc.rotation(angle, x=w / 2, y=h / 2):
            doc.text(x, y, text)
    return doc.output()


def _v_stamp(work, params, reader, mapping, enc, _defaults=None):
    PDF, _ = _need_pypdf()
    d = _defaults or {"angle": 0, "opacity": 1.0, "size": 28,
                      "color": "#b4b4b4"}
    text = str(params.get("text", "")).strip()
    if not text:
        raise _StepError("stamp needs text")
    idx, err = _parse_pages(params.get("pages", "all"), len(work))
    if err:
        raise _StepError(err)
    size = int(params.get("size", d["size"]))
    angle = float(params.get("rotate", d["angle"]))
    color = str(params.get("color", d["color"]))
    opacity = float(params.get("opacity", d["opacity"]))
    for i in idx:
        p = work[i]
        w = float(p.mediabox.width)
        h = float(p.mediabox.height)
        seal_bytes = _seal_pdf(text, w, h, size, angle, color, opacity)
        seal = PDF.PdfReader(io.BytesIO(bytes(seal_bytes))).pages[0]
        seal.add_transformation(PDF.Transformation().translate(
            tx=float(p.mediabox.left), ty=float(p.mediabox.bottom)))
        p.merge_page(seal)
    return (work, f"stamped “{text}” on {len(idx)} page(s)", mapping, enc)


def _v_watermark(work, params, reader, mapping, enc):
    return _v_stamp(work, params, reader, mapping, enc,
                    _defaults={"angle": 45, "opacity": 0.25, "size": 48,
                               "color": "#c8c8c8"})


def _v_page_numbers(work, params, reader, mapping, enc):
    PDF, _ = _need_pypdf()
    start = int(params.get("start", 1))
    fmt = str(params.get("format", "{n}"))
    pos = params.get("position", "bottom-center")
    total = len(work)
    for k, p in enumerate(work):
        label = fmt.replace("{n}", str(start + k)).replace("{total}",
                                                           str(total))
        w = float(p.mediabox.width)
        h = float(p.mediabox.height)
        seal_bytes = _seal_pdf(label, w, h, int(params.get("size", 11)),
                               0, "#505050", 1.0)
        seal = PDF.PdfReader(io.BytesIO(bytes(seal_bytes))).pages[0]
        dy = (-(h / 2) + 24) if pos.startswith("bottom") \
            else ((h / 2) - 24) if pos.startswith("top") else 0
        seal.add_transformation(PDF.Transformation().translate(
            tx=float(p.mediabox.left), ty=float(p.mediabox.bottom) + dy))
        p.merge_page(seal)
    return (work, f"numbered {total} page(s) from {start} as '{fmt}' "
            f"({pos})", mapping, enc)


def _v_overlay_pdf(work, params, reader, mapping, enc):
    other, err = _open_reader(params.get("source", ""),
                              params.get("password", ""))
    if err:
        raise _StepError(err)
    PDF, _ = _need_pypdf()
    stamp = other.pages[0]
    idx, perr = _parse_pages(params.get("pages", "all"), len(work))
    if perr:
        raise _StepError(perr)
    for i in idx:
        p = work[i]
        st = copy.deepcopy(stamp)
        st.add_transformation(PDF.Transformation().translate(
            tx=float(p.mediabox.left), ty=float(p.mediabox.bottom)))
        p.merge_page(st)
    return (work, f"overlaid page 1 of "
            f"{os.path.basename(str(params.get('source')))} onto "
            f"{len(idx)} page(s)", mapping, enc)


def _v_fill_form(work, params, reader, mapping, enc):
    fields = params.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        raise _StepError("fields must be a non-empty object")
    PDF, _ = _need_pypdf()
    tmp = PDF.PdfWriter()
    for p in work:
        tmp.add_page(p)
    try:
        for pg in tmp.pages:
            tmp.update_page_form_field_values(pg, fields)
    except Exception as e:
        raise _StepError(f"could not fill: {e}")
    buf = io.BytesIO()
    tmp.write(buf)
    buf.seek(0)
    rr = PDF.PdfReader(buf)
    work = [copy.deepcopy(p) for p in rr.pages]
    return (work, f"filled {len(fields)} field(s): "
            f"{', '.join(list(fields)[:6])}", mapping, enc)


def _v_encrypt(work, params, reader, mapping, enc):
    pwd = str(params.get("password", ""))
    if not pwd:
        raise _StepError("encrypt needs password")
    return work, "will seal at write time", mapping, \
        {"password": pwd, "owner_password": params.get("owner_password")}


_VERBS = {
    "split_two_up": _v_split_two_up,
    "select": _v_select,
    "merge": _v_merge,
    "rotate": _v_rotate,
    "crop": _v_crop,
    "scale": _v_scale,
    "n_up": _v_n_up,
    "stamp": _v_stamp,
    "watermark": _v_watermark,
    "page_numbers": _v_page_numbers,
    "overlay_pdf": _v_overlay_pdf,
    "fill_form": _v_fill_form,
    "encrypt": _v_encrypt,
}
