"""The S2 composition grid (Track 6): enumeration, ordering, gating, stamps.

Style follows test_regression's pinned ladder-order test: imaging is
monkeypatched with sentinels, so these assert call order and set membership
with no OCR and no real geometry.
"""
import pytest

from mib import config, imaging
from mib.stages import render


# ---------------------------------------------------------------------------
# _orientation_chains: canonical order inside one orientation frame


def test_corrections_run_in_the_orientation_frame(monkeypatch):
    """orientation -> deskew -> band realign, with the detectors re-run
    IN-FRAME: a turned+shredded page gets deshred after the turn, which the
    flat ladder never produced (its deshred ran only at 0 degrees)."""
    turned, deskewed, deshredded = object(), object(), object()
    seen = {}
    monkeypatch.setattr(imaging, "turn", lambda g, q: turned)
    monkeypatch.setattr(imaging, "rotate", lambda g, deg: deskewed)

    def fake_realign(base):
        seen["realign_arg"] = base
        return deshredded
    monkeypatch.setattr(imaging, "realign_bands", fake_realign)
    monkeypatch.setattr(imaging, "realign_local", lambda base: None)

    chains = dict(render._orientation_chains(
        object(), 1, 3.0, ("skew", "turn1", "turn3", "deshred", "local")))
    assert chains[("turn1", "skew")] is deskewed
    assert chains[("turn1", "skew", "deshred")] is deshredded
    assert seen["realign_arg"] is deskewed        # deshred saw the corrected frame


def test_turn_frame_below_min_skew_keeps_the_bare_turn_name(monkeypatch):
    monkeypatch.setattr(imaging, "turn", lambda g, q: object())
    monkeypatch.setattr(imaging, "rotate", lambda g, deg: g)
    monkeypatch.setattr(imaging, "realign_bands", lambda base: None)
    monkeypatch.setattr(imaging, "realign_local", lambda base: None)
    chains = dict(render._orientation_chains(object(), 3, 0.25, ("turn3",)))
    assert list(chains) == [("turn3",)]           # sub-MIN_SKEW claims no `skew`


def test_geom_set_gates_each_module(monkeypatch):
    monkeypatch.setattr(imaging, "rotate", lambda g, deg: object())
    monkeypatch.setattr(imaging, "realign_bands", lambda base: object())
    monkeypatch.setattr(imaging, "realign_local", lambda base: None)
    chains = dict(render._orientation_chains(object(), 0, 3.0, ("deshred",)))
    # skew not in the set: no deskew emitted, deshred runs on the raw frame
    assert list(chains) == [("deshred",)]


def test_grid_floor_covers_the_ladder_geometry(monkeypatch):
    """Every image the flat ladder produced has a pixel-identical counterpart
    in the grid's full enumeration (base + expanded orientations) — the -0.21
    early-stop lesson as a test: under-bar pages lose nothing."""
    deskewed, deshredded, turned = object(), object(), object()
    turned_deskewed = object()
    monkeypatch.setattr(imaging, "skew_angle", lambda g: 3.0)
    monkeypatch.setattr(imaging, "rotate",
                        lambda g, deg: turned_deskewed if g is turned else deskewed)
    monkeypatch.setattr(imaging, "turn", lambda g, q: turned)
    monkeypatch.setattr(imaging, "realign_bands",
                        lambda base: deshredded if base is deskewed else None)
    monkeypatch.setattr(imaging, "realign_local", lambda base: None)

    gray = object()
    ladder_images = {img for _n, img in render._restorations(gray)}
    grid_images = set()
    for q in (0, 1, 3):
        for _chain, img in render._orientation_chains(
                gray, q, 3.0, ("skew", "turn1", "turn3", "deshred", "local")):
            grid_images.add(img)
    assert ladder_images <= grid_images


# ---------------------------------------------------------------------------
# determinism


def test_enumeration_is_deterministic(monkeypatch):
    monkeypatch.setattr(imaging, "turn", lambda g, q: object())
    monkeypatch.setattr(imaging, "rotate", lambda g, deg: object())
    monkeypatch.setattr(imaging, "realign_bands", lambda base: object())
    monkeypatch.setattr(imaging, "realign_local", lambda base: None)
    names = [
        [c for c, _ in render._orientation_chains(
            object(), q, 2.0, ("skew", "turn1", "turn3", "deshred", "local"))]
        for q in (0, 1, 3)]
    names2 = [
        [c for c, _ in render._orientation_chains(
            object(), q, 2.0, ("skew", "turn1", "turn3", "deshred", "local"))]
        for q in (0, 1, 3)]
    assert names == names2


# ---------------------------------------------------------------------------
# page_score: frozen lexicon + the m_guards guards


def test_page_score_ignores_watermark_lines():
    assert render.page_score(["SAMPLE DENIAL", "COPY", "VOID SPECIMEN"]) == 0


def test_page_score_needs_two_tokens_for_label_credit():
    assert render.page_score(["Finding"]) == 0
    assert render.page_score(["Finding: APPROVED"]) == 1


def test_page_score_counts_frozen_values():
    lines = ["Case ID: MIB-000123", "Home World: Wolf-1061c", "junk ORION_GRAYS"]
    # 2 label lines + MIB id + world word + species word
    assert render.page_score(lines) == 5


def test_page_score_is_not_saturated_by_boilerplate():
    """The m_wmk scenario: a page that is all watermark + furniture must stay
    under the weak bar so the optical rescue still runs."""
    lines = ["SAMPLE DENIAL"] * 10 + ["Packet MIB-000061 / page 1"]
    assert render.page_score(lines) < render.WEAK_BAR


# ---------------------------------------------------------------------------
# plan resolution + stamps


def test_ladder_aliases_the_historical_stamp():
    assert config._restore_for(config.GRID_PRESETS["ladder"]) == "bands+local"


def test_grid_stamp_and_override_stamps_are_distinct(monkeypatch):
    base = config._restore_for(config.GRID_PRESETS["grid"])
    assert base == "grid"
    tweaked = dict(config.GRID_PRESETS["grid"], opt_base="raw")
    assert config._restore_for(tweaked) == "grid[opt_base=raw]"
    lastr = dict(config.GRID_PRESETS["grid"], last_resort="psm3")
    assert config._restore_for(lastr) == "grid[last_resort=psm3]"
    stamps = {config._restore_for(p) for p in
              (config.GRID_PRESETS["ladder"], config.GRID_PRESETS["grid"], tweaked, lastr)}
    assert len(stamps) == 4


def test_ladder_ignores_grid_override_envs(monkeypatch):
    monkeypatch.setenv("MIB_PLAN", "ladder")
    monkeypatch.setenv("MIB_OPT_BASE", "frames")
    assert config.grid_plan()["opt_base"] == "raw"


def test_grid_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("MIB_PLAN", "grid")
    monkeypatch.setenv("MIB_GEOM_SET", "skew,deshred")
    monkeypatch.setenv("MIB_LAST_RESORT", "psm3")
    plan = config.grid_plan()
    assert plan["geom"] == ("skew", "deshred")
    assert plan["last_resort"] == "psm3"


# ---------------------------------------------------------------------------
# damage-marker fuzzy rejection (the widened-regex diff's lesson)


@pytest.mark.parametrize("mangle", [
    "[NAME CUT OUT}", "(FEE STATUS OBSCURED}", "MAME CUT OUT", "NAME GUT OUT",
    "SPEQES WHITEOUT", "ILLEGIBLE]", "[PURPOSE", "INAME CUT OUT]",
])
def test_mangled_damage_markers_are_not_values(mangle):
    from mib.parse import valid_value
    assert not valid_value("applicant_name", mangle)


@pytest.mark.parametrize("legit", [
    "Veeul Ixoul", "reactor maintenance", "research", "archive audit",
    "Wolf-1061c", "ORION_GRAYS", "cultural exchange",
])
def test_legit_values_survive_the_marker_check(legit):
    from mib.parse import _damage_markerish
    assert not _damage_markerish(legit)
