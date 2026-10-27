"""A small French plan, written with ezdxf, for the ADR-375 tests.

Every text a plan can carry, one each:

* model space: a room name, a pipe with an SHX diameter code, an underlined
  title (SHX toggle — not handled), a date FIELD, a paragraph formatted
  whole (font, height), a paragraph with a word in red inside, a measure
  alone, two dimensions with a typed text (one keeps the measured value
  ``<>``), a leader;
* a title block (``CARTOUCHE``): a fixed label and an attribute definition
  (default text and prompt), inserted once with its title;
* a paper layout « Plan RDC » with the scale;
* an anonymous ``*T`` block: what a table SHOWS.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.render import mleader

#: The French texts, and the English a test translator gives them.
ENGLISH = {
    "SÉJOUR": "LIVING ROOM",
    "Ø 12 tube acier": "Ø 12 steel pipe",
    "Mur porteur\nbéton armé": "Load-bearing wall\nreinforced concrete",
    "Mur rouge porteur": "Load-bearing red wall",
    "<> env.": "approx. <>",
    "Cote à vérifier": "Dimension to be checked",
    "Voir détail": "See detail",
    "Dessiné par": "Drawn by",
    "Titre": "Title",
    "Titre du plan": "Plan title",
    "Plan du rez-de-chaussée": "First floor plan",
    "Échelle 1:100": "Scale 1:100",
    "Surface": "Area",
    "Entrée": "Entrance hall and coat closet",
}


def plan(path: Path) -> Path:
    """Write the fixture plan at ``path``; return it."""
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("PIECES", color=3)  # green: its twins must be green too
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 4000), (0, 4000)], close=True)
    msp.add_text("SÉJOUR", height=200, dxfattribs={"layer": "PIECES"}).set_placement((800, 2000))
    msp.add_text("Entrée", height=200, dxfattribs={"layer": "PIECES"}).set_placement((800, 1000))
    msp.add_text("%%c 12 tube acier", height=100).set_placement((100, 3500))
    msp.add_text("%%uTITRE%%u", height=100).set_placement((100, 3300))
    msp.add_text('Date : %<\\AcVar Date \\f "dd/MM/yyyy">%', height=100).set_placement((100, 3100))
    msp.add_mtext("{\\fArial|b1;\\H120;Mur porteur\\Pbéton armé}").set_location((300, 3800))
    msp.add_mtext("Mur {\\C1;rouge} porteur").set_location((300, 2800))
    msp.add_mtext("1504,68").set_location((300, 2600))
    first = msp.add_linear_dim(base=(0, -500), p1=(0, 0), p2=(6000, 0), text="<> env.")
    first.render()
    second = msp.add_linear_dim(
        base=(-500, 0), p1=(0, 0), p2=(0, 4000), angle=90, text="Cote à vérifier"
    )
    second.render()
    leader = msp.add_multileader_mtext("Standard")
    leader.set_content("Voir détail", char_height=100)
    leader.add_leader_line(mleader.ConnectionSide.left, [ezdxf.math.Vec2(3000, 2000)])
    leader.build(insert=ezdxf.math.Vec2(4000, 3000))
    block = doc.blocks.new("CARTOUCHE")
    block.add_text("Dessiné par", height=80).set_placement((0, 200))
    block.add_attdef(
        "TITRE", (0, 0), "Titre", dxfattribs={"height": 120, "prompt": "Titre du plan"}
    )
    insert = msp.add_blockref("CARTOUCHE", (0, -1500))
    insert.add_auto_attribs({"TITRE": "Plan du rez-de-chaussée"})
    sheet = doc.layouts.new("Plan RDC")
    sheet.add_text("Échelle 1:100", height=5).set_placement((20, 20))
    table = doc.blocks.new_anonymous_block(type_char="T")
    table.add_mtext("Surface").set_location((0, 0))
    doc.saveas(path)
    return path


def text_of(path: Path, handle: str) -> str:
    """The text of the entity ``handle`` in the drawing at ``path``."""
    doc = ezdxf.readfile(path)
    entity = doc.entitydb[handle]
    kind = entity.dxftype()
    if kind == "MTEXT":
        return str(entity.text)
    if kind == "MULTILEADER":
        return str(entity.get_mtext_content())
    return str(entity.dxf.text)
