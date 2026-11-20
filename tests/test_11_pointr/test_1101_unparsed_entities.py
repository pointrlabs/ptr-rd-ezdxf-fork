#  Copyright (c) 2026, Pointr
#  License: MIT License
"""The Pointr changes to bbox.extents(), disassemble and the drawing frontend
must not fail on what real CAD files contain.

Production failures these pin down:
- `Invalid DXF attribute "layer" for entity POSITIONMARKER / ARCALIGNEDTEXT /
  PLANESURFACE`: entity types ezdxf cannot parse load as DXFTagStorage, which
  has no layer attribute; extents(doc=...) read `entity.dxf.layer` of every
  entity.
- extents() without `doc` raised AttributeError, and ezdxf calls it without
  `doc` itself (zoom, select, xclip, MULTILEADER block content, text2path).
- POLYFACE / POLYMESH: make_primitive() raised TypeError, so they were left out
  of every bounding box.
"""
from __future__ import annotations

import io
import logging

import pytest

import ezdxf
from ezdxf import bbox, disassemble
from ezdxf._pointr import _seen
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.recorder import Recorder
from ezdxf.entities import DXFGraphic, DXFTagStorage
from ezdxf.entities.factory import ENTITY_CLASSES
from ezdxf.math import BoundingBox, Vec2

# entity types seen in customer CAD files that ezdxf does not parse
UNPARSED = ["ARCALIGNEDTEXT", "POSITIONMARKER", "PLANESURFACE", "AECC_COGO_POINT"]


def _room(msp, layer="WALLS"):
    for a, b in [((0, 0), (20, 0)), ((20, 0), (20, 10)), ((20, 10), (0, 10)),
                 ((0, 10), (0, 0)), ((10, 0), (10, 10))]:
        msp.add_line(a, b, dxfattribs={"layer": layer})


@pytest.fixture
def doc():
    d = ezdxf.new("R2010")
    d.layers.add("WALLS")
    _room(d.modelspace())
    blk = d.blocks.new("DESK")
    blk.add_lwpolyline([(0, 0), (1.6, 0), (1.6, 0.8), (0, 0.8)], close=True)
    d.modelspace().add_blockref("DESK", (2, 2))
    return d


def _entity_tags(dxftype: str, handle: str, owner: str, layer: str, *, graphic=True) -> str:
    if not graphic:  # no AcDbEntity subclass at all
        return f"  0\n{dxftype}\n  5\n{handle}\n330\n{owner}\n"
    return (
        f"  0\n{dxftype}\n  5\n{handle}\n330\n{owner}\n100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDb{dxftype.title()}\n  1\nTEXT\n 10\n5.0\n 20\n5.0\n 30\n0.0\n 40\n1.0\n"
    )


def with_unparsed(doc, dxftype: str, *, block: str = "", graphic=True, layer="WALLS", handle="FFFF0"):
    """`doc` re-read with one `dxftype` entity in the model space (or in `block`),
    the way it comes in a customer file."""
    stream = io.StringIO()
    doc.write(stream)
    text = stream.getvalue()
    if block:
        owner = doc.blocks.get(block).block_record_handle
        start = text.index(f"  0\nBLOCK\n")
        start = text.index(f"\n  2\n{block}\n", start)
        at = text.index("  0\nENDBLK\n", start)
    else:
        owner = doc.modelspace().block_record_handle
        at = text.index("  0\nENDSEC\n  0\nSECTION\n  2\nOBJECTS")
    tags = _entity_tags(dxftype, handle, owner, layer, graphic=graphic)
    return ezdxf.read(io.StringIO(text[:at] + tags + text[at:]))


def _unparsed(layout, dxftype):
    found = [e for e in layout if e.dxftype() == dxftype]
    assert len(found) == 1
    return found[0]


def same_box(a: BoundingBox, b: BoundingBox) -> bool:
    return a.has_data == b.has_data and (
        not a.has_data or (a.extmin.isclose(b.extmin) and a.extmax.isclose(b.extmax))
    )


@pytest.mark.parametrize("dxftype", UNPARSED)
def test_unparsed_entity_has_no_layer_attribute(doc, dxftype):
    e = _unparsed(with_unparsed(doc, dxftype).modelspace(), dxftype)
    assert isinstance(e, DXFTagStorage)
    assert not e.dxf.is_supported("layer")


@pytest.mark.parametrize("graphic", [True, False])
@pytest.mark.parametrize("dxftype", UNPARSED)
def test_extents_ignores_unparsed_entity(doc, dxftype, graphic):
    expected = bbox.extents(doc.modelspace(), doc=doc)
    hostile = with_unparsed(doc, dxftype, graphic=graphic)
    msp = hostile.modelspace()
    assert same_box(bbox.extents(msp, doc=hostile), expected)
    assert same_box(bbox.extents(msp, fast=True, cache=bbox.Cache(), doc=hostile), expected)


@pytest.mark.parametrize("dxftype", UNPARSED)
def test_extents_of_block_with_unparsed_entity(doc, dxftype):
    expected = bbox.extents(doc.blocks.get("DESK"), doc=doc)
    hostile = with_unparsed(doc, dxftype, block="DESK")
    _unparsed(hostile.blocks.get("DESK"), dxftype)
    assert same_box(bbox.extents(hostile.blocks.get("DESK"), doc=hostile), expected)
    assert same_box(bbox.extents(hostile.modelspace(), doc=hostile),
                    bbox.extents(doc.modelspace(), doc=doc))


@pytest.mark.parametrize("dxftype", UNPARSED)
def test_draw_layout_with_unparsed_entity(doc, dxftype):
    def draw(d):
        backend = Recorder()
        Frontend(RenderContext(d), backend).draw_layout(d.modelspace())
        return len(list(backend.player().recordings()))

    hostile = with_unparsed(doc, dxftype)
    assert draw(hostile) == draw(doc)
    hostile = with_unparsed(doc, dxftype, block="DESK")
    assert draw(hostile) == draw(doc)


def test_extents_without_doc_is_upstream(doc):
    msp = doc.modelspace()
    expected = BoundingBox()
    for box in bbox.multi_flat(msp):
        expected.extend(box)
    assert same_box(bbox.extents(msp), expected)
    hostile = with_unparsed(doc, "POSITIONMARKER")
    assert same_box(bbox.extents(hostile.modelspace()), expected)


def test_ezdxf_callers_of_extents_without_doc(doc):
    """zoom, select and MULTILEADER rendering call extents() without doc."""
    from ezdxf import select, zoom

    msp = doc.modelspace()
    zoom.extents(msp)
    window = select.Window((-1, -1), (21, 11))
    assert len(select.bbox_inside(window, msp)) == len(msp)

    ml = msp.add_multileader_block(style="Standard")
    ml.set_content(name="DESK")
    ml.add_leader_line(ezdxf.render.mleader.ConnectionSide.left, [(30, 30)])
    ml.build(insert=Vec2(25, 25))
    backend = Recorder()
    Frontend(RenderContext(doc), backend).draw_entities(msp.query("MULTILEADER"))
    assert len(list(backend.player().recordings())) > 0


def test_extents_entity_on_missing_layer_is_drawn():
    d = ezdxf.new()
    _room(d.modelspace(), layer="NOT_IN_TABLE")
    assert not d.layers.has_entry("NOT_IN_TABLE")
    box = bbox.extents(d.modelspace(), doc=d)
    assert box.extmin.isclose((0, 0)) and box.extmax.isclose((20, 10))


@pytest.mark.parametrize("hide", ["invisible", "off", "frozen"])
def test_extents_leaves_out_hidden_entities(hide):
    d = ezdxf.new()
    d.layers.add("WALLS")
    _room(d.modelspace())
    d.layers.add("HIDDEN")
    line = d.modelspace().add_line((100, 100), (101, 100), dxfattribs={"layer": "HIDDEN"})
    if hide == "invisible":
        line.dxf.invisible = 1
    elif hide == "off":
        d.layers.get("HIDDEN").off()
    else:
        d.layers.get("HIDDEN").freeze()
    box = bbox.extents(d.modelspace(), doc=d)
    assert box.extmax.isclose((20, 10))


def test_extents_of_zero_size_boxes_only():
    d = ezdxf.new()
    msp = d.modelspace()
    msp.add_point((1, 1))
    msp.add_point((5, 3))
    box = bbox.extents(msp, doc=d)
    assert box.extmin.isclose((1, 1)) and box.extmax.isclose((5, 3))


def test_extents_keeps_the_pointr_outlier_filters():
    d = ezdxf.new()
    d.layers.add("WALLS")
    msp = d.modelspace()
    for i in range(10):
        _room(msp)
    msp.add_line((1e6, 1e6), (1e6 + 1, 1e6))  # stray entity far away
    msp.add_text("HUGE", height=1e6).set_placement((0, 0))  # absurd text box
    box = bbox.extents(msp, doc=d)
    assert box.extmax.isclose((20, 10))


@pytest.mark.parametrize("kind", ["polyface", "polymesh"])
def test_meshes_are_primitives_and_in_extents(kind):
    d = ezdxf.new()
    msp = d.modelspace()
    _room(msp)
    if kind == "polyface":
        mesh = msp.add_polyface()
        mesh.append_face([(30, 30), (40, 30), (40, 40)])
    else:
        mesh = msp.add_polymesh((2, 2))
        for (m, n), p in zip([(0, 0), (0, 1), (1, 0), (1, 1)], [(30, 30), (30, 40), (40, 30), (40, 40)]):
            mesh.set_mesh_vertex((m, n), p)
    p = disassemble.make_primitive(mesh)
    assert p.mesh is not None and p.bbox().has_data
    box = bbox.extents(msp, doc=d)
    assert box.extmax.isclose((40, 40))


def test_property_override_functions_are_applied(doc):
    msp = doc.modelspace()
    backend = Recorder()
    frontend = Frontend(RenderContext(doc), backend)

    def hide_walls(entity, properties):
        if properties.layer == "WALLS":
            properties.is_visible = False

    frontend.push_property_override_function(hide_walls)
    frontend.draw_entities(msp.query("LINE"))
    assert len(list(backend.player().recordings())) == 0


def _one_of_every_graphic_type(d):
    msp = d.modelspace()
    created = []
    for dxftype, cls in sorted(ENTITY_CLASSES.items()):
        if not issubclass(cls, DXFGraphic) or dxftype in ("VERTEX", "SEQEND", "ATTRIB", "VIEWPORT"):
            continue
        try:
            msp.new_entity(dxftype, {})
        except Exception:
            continue  # needs data a default entity does not have
        created.append(dxftype)
    return created


def test_every_entity_type_through_extents_disassemble_and_drawing(doc):
    """No entity type may fail extents(), disassemble or the drawing frontend:
    a new failure here is the next production failure."""
    d = doc
    for i, dxftype in enumerate(UNPARSED):
        d = with_unparsed(d, dxftype, handle=f"FFFF{i}")
        d = with_unparsed(d, dxftype, block="DESK", handle=f"FFFE{i}")
    # default entities cannot all be written to a file: added after the re-reads
    created = _one_of_every_graphic_type(d)
    assert {"LINE", "MTEXT", "MULTILEADER", "POLYLINE", "MESH"} <= set(created)
    msp = d.modelspace()
    assert {e.dxftype() for e in msp} >= set(UNPARSED)
    # default DIMENSION, LEADER, HELIX, ... are malformed (no text midpoint, no
    # vertices): skipped one by one with a warning, as the drawing skips them
    box = bbox.extents(msp, doc=d)
    assert box.inside((0, 0)) and box.inside((20, 10))
    bbox.extents(msp, fast=True, cache=bbox.Cache(), doc=d)
    Frontend(RenderContext(d), Recorder()).draw_layout(msp)


def test_skipped_entity_is_a_warning_once(doc, caplog):
    from ezdxf._pointr import log_skipped_entity

    logger = logging.getLogger("ezdxf")
    _seen.clear()
    line = doc.modelspace().query("LINE").first
    with caplog.at_level(logging.DEBUG, logger="ezdxf"):
        log_skipped_entity(logger, "test", line, ValueError("x"))
        log_skipped_entity(logger, "test", line, ValueError("y"))
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.WARNING, logging.DEBUG]
