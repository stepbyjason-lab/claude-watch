import pytest
from pathlib import Path

from scripts.scenes import (
    apply_coverage_floor,
    apply_budget_cap,
    detect_scenes,
    _build_scene_vf,
    Scene,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10s.mp4"


def test_build_scene_vf_default_has_no_prefilter():
    assert _build_scene_vf(0.30, "") == "select='gt(scene,0.3)',showinfo,metadata=print"


def test_build_scene_vf_prepends_prefilter():
    vf = _build_scene_vf(0.10, "crop=100:100:0:0,")
    assert vf == "crop=100:100:0:0,select='gt(scene,0.1)',showinfo,metadata=print"


def test_apply_coverage_floor_inserts_at_max_gap_intervals():
    """One detected boundary at t=5 in a 60s video; max_gap_s=45 → floor at t=50."""
    scenes = [Scene(t=0.0, score=1.0, kind="detected"), Scene(t=5.0, score=0.9, kind="detected")]
    out = apply_coverage_floor(scenes, duration_s=60.0, max_gap_s=45.0)
    floor_times = [s.t for s in out if s.kind == "floor"]
    assert floor_times == [50.0]


def test_apply_coverage_floor_inserts_multiple_for_long_static_run():
    """Gap of 200s with max_gap=45 should insert floors at 5+45=50, 95, 140, 185."""
    scenes = [Scene(t=5.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(scenes, duration_s=205.0, max_gap_s=45.0)
    floor_times = sorted(s.t for s in out if s.kind == "floor")
    assert floor_times == [50.0, 95.0, 140.0, 185.0]


def test_apply_coverage_floor_no_op_when_gaps_under_threshold():
    scenes = [Scene(t=0.0, score=1.0, kind="detected"), Scene(t=10.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(scenes, duration_s=20.0, max_gap_s=45.0)
    assert all(s.kind == "detected" for s in out)


# --- end-of-video tail anchor (slides-mode opt-in) -------------------------------
# The coverage floor steps by max_gap from the last scene with `while t < duration`,
# so the final <=max_gap tail is uncovered: a slide that appears only in those last
# seconds is never extracted as a candidate. `include_tail_anchor=True` (slides mode
# only) guarantees one floor near duration-eps. Default stays False so classic mode
# is byte-identical to upstream.


def test_tail_anchor_default_false_leaves_short_tail_uncovered():
    """Default behavior unchanged: a short final tail gets no floor."""
    scenes = [Scene(t=0.0, score=1.0, kind="detected"), Scene(t=10.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(scenes, duration_s=12.0, max_gap_s=20.0)
    assert all(s.kind == "detected" for s in out)


def test_tail_anchor_covers_short_final_tail_when_opted_in():
    """opt-in: final 2s tail (< max_gap) gets a floor at duration-eps so the last
    slide is extractable."""
    scenes = [Scene(t=0.0, score=1.0, kind="detected"), Scene(t=10.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(
        scenes, duration_s=12.0, max_gap_s=20.0, include_tail_anchor=True
    )
    floor_times = [round(s.t, 3) for s in out if s.kind == "floor"]
    assert floor_times == [11.5]  # duration_s - 0.5


def test_tail_anchor_fills_uncovered_tail_after_floor_run():
    """Realistic case: floors land at 50/95/140/185 then 185->205 (20s) is the
    uncovered tail; the anchor fills it at 204.5."""
    scenes = [Scene(t=5.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(
        scenes, duration_s=205.0, max_gap_s=45.0, include_tail_anchor=True
    )
    floor_times = sorted(round(s.t, 3) for s in out if s.kind == "floor")
    assert floor_times == [50.0, 95.0, 140.0, 185.0, 204.5]


def test_tail_anchor_skips_when_scene_already_near_end():
    """No duplicate anchor: a detected scene 0.2s before end already covers the tail."""
    scenes = [Scene(t=0.0, score=1.0, kind="detected"), Scene(t=11.8, score=0.9, kind="detected")]
    out = apply_coverage_floor(
        scenes, duration_s=12.0, max_gap_s=20.0, include_tail_anchor=True
    )
    assert not [s for s in out if s.kind == "floor"]


def test_tail_anchor_skips_when_floor_already_near_end():
    """Guard also fires against the floor run: a floor at t=50 in a 50.4s video is
    within tail_eps of duration, so no redundant anchor at 49.9."""
    scenes = [Scene(t=5.0, score=1.0, kind="detected")]
    out = apply_coverage_floor(
        scenes, duration_s=50.4, max_gap_s=45.0, include_tail_anchor=True
    )
    floor_times = sorted(round(s.t, 3) for s in out if s.kind == "floor")
    assert floor_times == [50.0]


def test_apply_budget_cap_drops_lowest_scoring_detected_first():
    scenes = [Scene(t=float(i), score=float(i), kind="detected") for i in range(10)]
    out = apply_budget_cap(scenes, max_frames=4)
    assert len(out) == 4
    kept_scores = sorted(s.score for s in out)
    assert kept_scores == [6.0, 7.0, 8.0, 9.0]


def test_apply_budget_cap_preserves_floor_boundaries():
    """Floor boundaries are coverage guarantees; should never be dropped."""
    scenes = [
        Scene(t=0.0, score=1.0, kind="detected"),
        Scene(t=1.0, score=0.5, kind="floor"),
        Scene(t=2.0, score=0.9, kind="detected"),
        Scene(t=3.0, score=0.4, kind="detected"),
    ]
    out = apply_budget_cap(scenes, max_frames=2)
    assert len(out) == 2
    assert any(s.kind == "floor" and s.t == 1.0 for s in out), "floor must be preserved"


def test_apply_budget_cap_returns_sorted_by_time():
    scenes = [Scene(t=5.0, score=0.9, kind="detected"), Scene(t=1.0, score=0.8, kind="detected")]
    out = apply_budget_cap(scenes, max_frames=10)
    assert [s.t for s in out] == [1.0, 5.0]


@pytest.mark.integration
def test_detect_scenes_finds_known_cuts_in_fixture():
    """sample_10s.mp4 has hard cuts at t=3 and t=6 (red→white→blue;
    red and green share luma so the fixture uses white for the middle)."""
    scenes = detect_scenes(FIXTURE, threshold=0.30)
    detected = [s for s in scenes if s.kind == "detected"]
    times = [round(s.t, 1) for s in detected]
    # t=0 always present; the two real cuts at ~3.0 and ~6.0
    assert any(2.8 <= t <= 3.2 for t in times), f"missing cut near 3s; got {times}"
    assert any(5.8 <= t <= 6.2 for t in times), f"missing cut near 6s; got {times}"
