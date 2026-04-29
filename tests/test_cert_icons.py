from zira_dashboard import cert_icons


def test_icon_for_known_forklift_returns_svg():
    svg = cert_icons.icon_for("Forklift Certified")
    assert svg is not None
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_icon_for_case_insensitive():
    a = cert_icons.icon_for("Forklift Certified")
    b = cert_icons.icon_for("FORKLIFT CERTIFIED")
    c = cert_icons.icon_for("forklift certified")
    assert a is not None
    assert a == b == c


def test_icon_for_strips_surrounding_whitespace():
    a = cert_icons.icon_for("Forklift Certified")
    b = cert_icons.icon_for("  Forklift Certified  ")
    assert a is not None
    assert a == b


def test_icon_for_unknown_returns_none():
    assert cert_icons.icon_for("Reach Truck Certified") is None
    assert cert_icons.icon_for("") is None


def test_cdl_automatics_and_manuals_share_icon():
    a = cert_icons.icon_for("CDL (Automatics) Certified")
    m = cert_icons.icon_for("CDL (Manuals) Certified")
    assert a is not None
    assert a == m


def test_dot_uses_wrench_distinct_from_others():
    dot = cert_icons.icon_for("DOT Certified")
    fork = cert_icons.icon_for("Forklift Certified")
    assert dot is not None
    assert dot != fork


def test_spotter_icon_distinct_from_cdl():
    spotter = cert_icons.icon_for("Spotter Truck Certified")
    cdl = cert_icons.icon_for("CDL (Automatics) Certified")
    assert spotter is not None
    assert cdl is not None
    assert spotter != cdl


def test_slug_for_known_certs():
    assert cert_icons.slug_for("Forklift Certified") == "forklift"
    assert cert_icons.slug_for("Spotter Truck Certified") == "spotter"
    assert cert_icons.slug_for("CDL (Manuals) Certified") == "cdl-manual"
    assert cert_icons.slug_for("CDL (Automatics) Certified") == "cdl-auto"
    assert cert_icons.slug_for("DOT Certified") == "dot"


def test_slug_for_case_insensitive_and_trimmed():
    assert cert_icons.slug_for("  forklift CERTIFIED  ") == "forklift"


def test_slug_for_unknown_returns_none():
    assert cert_icons.slug_for("Reach Truck Certified") is None
    assert cert_icons.slug_for("") is None


def test_all_data_returns_svg_and_slug_for_each_cert():
    data = cert_icons.all_data()
    # Every key from _CERT_ICONS is present.
    assert set(data.keys()) == {
        "forklift certified",
        "cdl (automatics) certified",
        "cdl (manuals) certified",
        "dot certified",
        "spotter truck certified",
    }
    # Each entry has both svg and slug.
    fork = data["forklift certified"]
    assert fork["svg"].startswith("<svg")
    assert fork["slug"] == "forklift"
    cdl_auto = data["cdl (automatics) certified"]
    assert cdl_auto["slug"] == "cdl-auto"
    cdl_manual = data["cdl (manuals) certified"]
    assert cdl_manual["slug"] == "cdl-manual"
    # CDL automatics + manuals share the same SVG.
    assert cdl_auto["svg"] == cdl_manual["svg"]
