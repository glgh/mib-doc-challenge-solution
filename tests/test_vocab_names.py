"""Name-part pool snap (row 52): the generator composes names from a 12x12
prefix x suffix grid, so out-of-pool tokens are misreads (0/61 correct on dev)
and snapping them toward the pool is a free roll; in-pool tokens are 95.9%
correct and must never be substituted."""
from mib import vocab


def test_out_of_pool_token_snaps_to_unique_best():
    assert vocab.snap("applicant_name", "Mirazam Zatari") == "Mirazarn Zatari"
    assert vocab.snap("applicant_name", "Lunex Nexrix") == "Lunax Nexrix"


def test_in_pool_tokens_are_never_substituted():
    # `Luix` and `Lurix` are BOTH real pool parts one glyph apart — a real
    # Luix must not become Lurix.
    assert vocab.snap("applicant_name", "Ludane Luix") == "Ludane Luix"
    assert vocab.snap("applicant_name", "Nexnax Lurix") == "Nexnax Lurix"


def test_lowercase_reads_normalize_to_capitalized():
    assert vocab.snap("applicant_name", "ixovara Tekix") == "Ixovara Tekix"


def test_damage_markers_and_garbage_pass_through():
    assert vocab.snap("applicant_name", "[NAME CUT OUT]") == "[NAME CUT OUT]"
    # ambiguous junk below the bar/margin stays as read, never fabricated
    assert vocab.snap("applicant_name", "Zzq Wm") == "Zzq Wm"
