"""The site's forms: submissions into settings and options, the refusals they name, and saved settings."""

import pytest
from starlette.datastructures import FormData

from golf.core.patches.extended_sram_defaults import BallSpin, SwingSpeed
from golf.core.patches.sram_defaults import VANILLA_CLUBS, VANILLA_NAME, Club
from golf.randomizer.build import PlayerOptions
from golf.randomizer.catalog import JP_ROM, US_ROM
from golf.randomizer.manifest import (
    DEFAULT_MERCY_POINT,
    ClubRules,
    Settings,
    standard_rules,
)
from golf.randomizer.roms import vanilla_rom
from golf.randomizer.wind import DIRECTION_PROFILES, SPEED_PROFILES
from server.forms import (
    CLUBS_BANNED,
    CLUBS_OVER_MAX,
    DRAW_RULE_CHOICES,
    INVALID,
    INVALID_NAME,
    MUSIC_CHOICES,
    NO_SOURCES,
    PARS,
    REQUIRED_BAG_BANNED,
    REQUIRED_BAG_OVER_MAX,
    ROMS_MISSING,
    RULE_CLUBS,
    DownloadState,
    FormError,
    FormState,
    SavedSettings,
    check_rom_hashes,
    fit,
    player_options_from_state,
    settings_from_state,
    to_save,
)


def submit(**changes) -> Settings:
    return settings_from_state(state(**changes))


def state(**changes) -> FormState:
    form = FormState.default()
    for name, value in changes.items():
        setattr(form, name, value)
    return form


def refusal(**changes) -> FormError:
    with pytest.raises(FormError) as caught:
        submit(**changes)
    return caught.value


def test_the_default_form_submits_the_default_settings():
    settings = settings_from_state(
        FormState.from_form(FormData([*FormState.default().to_pairs()]))
    )
    assert settings == Settings()


def test_both_vanilla_sources_are_enabled():
    assert FormState.default().sources == {US_ROM, JP_ROM}
    assert submit(sources={US_ROM, JP_ROM}).sources == frozenset({US_ROM, JP_ROM})


def test_the_form_lists_every_par_music_and_club_but_the_putter():
    assert PARS == (72, 71, 70)
    assert MUSIC_CHOICES[0] == "random" and len(MUSIC_CHOICES) == 9
    assert Club.PT not in RULE_CLUBS and len(RULE_CLUBS) == 15


def test_the_mercy_point_is_always_the_default_whatever_is_sent():
    form = FormData([*FormState.default().to_pairs(), ("mercy_point", "3")])
    assert (
        settings_from_state(FormState.from_form(form)).mercy_point
        == DEFAULT_MERCY_POINT
    )


def test_every_field_reaches_the_settings():
    settings = submit(
        par="70",
        sources={US_ROM},
        allow_family_repeats=True,
        draw_rule="experts_0",
        wind_speed="storm_passing",
        wind_direction="tailwind_out",
        music="jp_france",
        clubs_max="10",
        banned={"1W", "sw"},
        required_bag={"3W", "5I", "PW"},
    )
    assert settings == Settings(
        par=70,
        sources=frozenset({US_ROM}),
        allow_family_repeats=True,
        constraints=standard_rules(70, 0),
        wind_speed_profile="storm_passing",
        wind_direction_profile="tailwind_out",
        music="jp_france",
        clubs=ClubRules(
            max=10,
            banned=frozenset({Club.W1, Club.SW}),
            required_bag=frozenset({Club.W3, Club.I5, Club.PW}),
        ),
    )


def test_a_submission_round_trips_through_its_pairs():
    submitted = state(
        par="71",
        sources={JP_ROM},
        allow_family_repeats=True,
        draw_rule="uniform",
        wind_speed="gentle",
        wind_direction="prevailing",
        banned={"2I"},
        required_bag={"1W"},
    )
    assert FormState.from_form(FormData([*submitted.to_pairs()])) == submitted


def test_the_form_offers_three_expert_caps_and_starts_on_the_default():
    assert DRAW_RULE_CHOICES == {"experts_0": 0, "experts_1": 1, "uniform": None}
    assert (
        DRAW_RULE_CHOICES[FormState.default().draw_rule] == Settings().experts_per_nine
    )
    for name, cap in DRAW_RULE_CHOICES.items():
        settings = submit(draw_rule=name, par="71")
        assert settings.experts_per_nine == cap
        assert settings.rules == standard_rules(71, cap)
    assert submit(draw_rule="experts_1").constraints is None


@pytest.mark.parametrize("value", ["", "experts_2", "expert_cap", "ceiling"])
def test_an_expert_cap_the_form_does_not_offer_is_refused(value):
    problem = refusal(draw_rule=value)
    assert (problem.reason, problem.values) == (INVALID, {"field": "draw_rule"})


def test_the_form_offers_every_wind_profile_and_starts_on_vanilla():
    assert (FormState.default().wind_speed, FormState.default().wind_direction) == (
        "vanilla",
        "vanilla",
    )
    for profile in SPEED_PROFILES:
        assert submit(wind_speed=profile).wind_speed_profile == profile
    for profile in DIRECTION_PROFILES:
        assert submit(wind_direction=profile).wind_direction_profile == profile


@pytest.mark.parametrize("field", ["wind_speed", "wind_direction"])
@pytest.mark.parametrize("value", ["", "hurricane", "VANILLA"])
def test_a_wind_profile_the_form_does_not_offer_is_refused(field, value):
    problem = refusal(**{field: value})
    assert (problem.reason, problem.values) == (INVALID, {"field": field})


def test_unknown_fields_and_blank_space_are_ignored():
    form = FormData(
        [
            ("par", " 72 "),
            ("sources", US_ROM),
            ("draw_rule", " experts_1 "),
            ("wind_speed", "vanilla"),
            ("wind_direction", "vanilla"),
            ("music", "random"),
            ("clubs_max", "14"),
            ("prng_seed", "x"),
        ]
    )
    assert settings_from_state(FormState.from_form(form)) == Settings(
        sources=frozenset({US_ROM})
    )


def test_no_required_bag_checked_means_players_choose():
    assert submit(required_bag=set()).clubs.required_bag is None


def test_no_sources_is_refused():
    assert refusal(sources=set()).reason == NO_SOURCES


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"par": ""}, "par"),
        ({"par": "73"}, "par"),
        ({"sources": {US_ROM, "nes_open_jp"}}, "sources"),
        ({"music": "nes_mars"}, "music"),
        ({"music": ""}, "music"),
        ({"clubs_max": "0"}, "clubs_max"),
        ({"clubs_max": "15"}, "clubs_max"),
        ({"clubs_max": "ten"}, "clubs_max"),
        ({"banned": {"PT"}}, "banned"),
        ({"banned": {"9W"}}, "banned"),
        ({"required_bag": {"PT"}}, "required_bag"),
    ],
)
def test_values_the_form_cannot_send_are_refused_naming_the_field(changes, field):
    problem = refusal(**changes)
    assert problem.reason == INVALID
    assert problem.values == {"field": field}


def test_a_required_bag_over_the_max_is_refused_counting_the_putter():
    problem = refusal(clubs_max="3", required_bag={"1W", "3W", "PW"})
    assert problem.reason == REQUIRED_BAG_OVER_MAX
    assert problem.values == {"count": 4, "max": 3}
    assert submit(clubs_max="4", required_bag={"1W", "3W", "PW"}).clubs.max == 4


def test_a_required_bag_holding_banned_clubs_is_refused():
    problem = refusal(banned={"1W", "SW", "2I"}, required_bag={"SW", "1W", "PW"})
    assert problem.reason == REQUIRED_BAG_BANNED
    assert problem.values == {"clubs": "1W SW"}


# -- Download ---------------------------------------------------------------------------------

RULES = ClubRules()
US_SHA1 = vanilla_rom(US_ROM).sha1
JP_SHA1 = vanilla_rom(JP_ROM).sha1


def download(
    name: str = "luigi", clubs: set[str] | None = None, **hashes: str
) -> DownloadState:
    return DownloadState(
        player_name=name,
        clubs={"1W", "PW"} if clubs is None else clubs,
        rom_hashes=hashes,
    )


def download_refusal(state: DownloadState, rules: ClubRules = RULES) -> FormError:
    with pytest.raises(FormError) as caught:
        player_options_from_state(state, rules)
    return caught.value


def test_a_download_submission_reads_name_clubs_and_rom_hashes():
    form = FormData(
        [
            ("player_name", "  luigi "),
            ("clubs", "1W"),
            ("clubs", "PW"),
            ("rom_nes_open_us", US_SHA1.upper()),
            ("rom_mario_open_jp", JP_SHA1),
            ("other", "ignored"),
        ]
    )
    state = DownloadState.from_form(form)
    assert state == DownloadState(
        "luigi", {"1W", "PW"}, {US_ROM: US_SHA1, JP_ROM: JP_SHA1}
    )
    assert DownloadState.from_form(FormData([*state.to_pairs()])) == state


def test_the_download_default_is_the_vanilla_name_and_bag_without_banned_clubs():
    state = DownloadState.default(ClubRules(banned=frozenset({Club.W1})))
    assert state.player_name == VANILLA_NAME
    assert state.clubs == {club.label for club in VANILLA_CLUBS} - {"1W", "PT"}


def test_the_download_default_for_a_locked_bag_is_that_bag():
    rules = ClubRules(required_bag=frozenset({Club.W1, Club.PW}))
    assert DownloadState.default(rules).clubs == {"1W", "PW"}


def test_player_options_upper_case_the_name_and_add_the_putter():
    options = player_options_from_state(download("luigi"), RULES)
    assert options.player_name == "LUIGI"
    assert options.clubs == {Club.W1, Club.PW, Club.PT}
    assert options.bgm is True


def test_a_locked_bag_ignores_the_submitted_clubs():
    rules = ClubRules(required_bag=frozenset({Club.W3, Club.I5}))
    options = player_options_from_state(download(clubs={"1W", "2W", "9W"}), rules)
    assert options.clubs == {Club.W3, Club.I5, Club.PT}


@pytest.mark.parametrize(
    "name, chars",
    [("", ""), ("   ", ""), ("ABCDEFGHIJK", ""), ("L-U1GI", "-1"), ("MARIÖ", "Ö")],
)
def test_a_name_the_game_cannot_store_is_refused(name, chars):
    problem = download_refusal(download(name))
    assert problem.reason == INVALID_NAME
    assert problem.values == {"chars": chars}


def test_a_ten_character_name_with_dots_and_spaces_is_allowed():
    assert (
        player_options_from_state(download("dr. mario."), RULES).player_name
        == "DR. MARIO."
    )


def test_an_unknown_club_is_refused():
    problem = download_refusal(download(clubs={"1W", "9W"}))
    assert problem.reason == INVALID
    assert problem.values == {"field": "clubs"}


def test_a_bag_holding_banned_clubs_is_refused():
    rules = ClubRules(banned=frozenset({Club.W1, Club.SW}))
    problem = download_refusal(download(clubs={"SW", "1W", "PW"}), rules)
    assert problem.reason == CLUBS_BANNED
    assert problem.values == {"clubs": "1W SW"}


def test_a_bag_over_the_max_is_refused_counting_the_putter():
    problem = download_refusal(download(clubs={"1W", "3W", "PW"}), ClubRules(max=3))
    assert problem.reason == CLUBS_OVER_MAX
    assert problem.values == {"count": 4, "max": 3}


def test_rom_hashes_must_match_every_required_rom():
    check_rom_hashes(
        download(nes_open_us=US_SHA1, mario_open_jp=JP_SHA1), (US_ROM, JP_ROM)
    )
    check_rom_hashes(download(nes_open_us=US_SHA1), (US_ROM,))


@pytest.mark.parametrize(
    "hashes, roms",
    [
        ({}, "NES Open Tournament Golf (USA), Mario Open Golf (Japan)"),
        ({"nes_open_us": US_SHA1}, "Mario Open Golf (Japan)"),
        ({"nes_open_us": US_SHA1, "mario_open_jp": US_SHA1}, "Mario Open Golf (Japan)"),
    ],
)
def test_a_missing_or_wrong_rom_hash_is_refused_naming_the_roms(hashes, roms):
    with pytest.raises(FormError) as caught:
        check_rom_hashes(download(**hashes), (US_ROM, JP_ROM))
    assert caught.value.reason == ROMS_MISSING
    assert caught.value.values == {"roms": roms}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, False),
        ({"clubs_max": "13"}, True),
        ({"banned": {"1W"}}, True),
        ({"required_bag": {"PW"}}, True),
    ],
)
def test_club_rules_count_as_set_only_away_from_the_defaults(changes, expected):
    assert state(**changes).has_club_rules() is expected


# -- Saved settings ---------------------------------------------------------------------------

VANILLA_LABELS = {club.label for club in VANILLA_CLUBS} - {"PT"}
SAVED = SavedSettings(
    player_name="LUIGI",
    clubs=frozenset({Club.W1, Club.W3, Club.I5, Club.PW, Club.SW, Club.PT}),
    bgm=False,
    swing=SwingSpeed.FAST,
    putt=SwingSpeed.SLOW,
    spin=BallSpin.BACK1,
)


def test_saved_settings_round_trip_through_json():
    assert SAVED.to_json() == {
        "v": 1,
        "name": "LUIGI",
        "clubs": ["1W", "3W", "5I", "PW", "SW", "PT"],
        "bgm": False,
        "swing": "fast",
        "putt": "slow",
        "spin": "back1",
    }
    assert SavedSettings.from_json(SAVED.to_json()) == SAVED


@pytest.mark.parametrize("data", [None, "LUIGI", [], 7, {}, {"v": 99}])
def test_a_record_that_is_not_an_object_or_holds_nothing_reads_as_vanilla(data):
    assert SavedSettings.from_json(data) == SavedSettings()


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", ""),
        ("name", "   "),
        ("name", "ABCDEFGHIJK"),
        ("name", "LU!GI"),
        ("name", 7),
        ("clubs", ["1W", "9W"]),
        ("clubs", "1W"),
        ("clubs", ["1W", 2]),
        ("clubs", [club.label for club in Club if club != Club.PT] + ["PT"]),
        ("bgm", "false"),
        ("bgm", 0),
        ("swing", "warp"),
        ("swing", 2),
        ("putt", "back1"),
        ("spin", "fast"),
        ("spin", None),
    ],
)
def test_an_invalid_field_reads_as_vanilla_and_keeps_the_rest(field, value):
    data = SAVED.to_json() | {field: value}
    loaded = SavedSettings.from_json(data)
    attr = "player_name" if field == "name" else field
    assert getattr(loaded, attr) == getattr(SavedSettings(), attr)
    for other in ("player_name", "clubs", "bgm", "swing", "putt", "spin"):
        if other != attr:
            assert getattr(loaded, other) == getattr(SAVED, other)


def test_unknown_fields_are_ignored_and_names_are_upper_cased():
    loaded = SavedSettings.from_json(
        SAVED.to_json() | {"name": "dr. mario", "hat": "red", "clubs": ["1w", "pw"]}
    )
    assert loaded.player_name == "DR. MARIO"
    assert loaded.clubs == {Club.W1, Club.PW, Club.PT}


def test_fitting_under_default_rules_starts_with_the_saved_settings():
    fitted = fit(SAVED, ClubRules(), abi=2)
    assert fitted.state == DownloadState(
        player_name="LUIGI",
        clubs={"1W", "3W", "5I", "PW", "SW"},
        bgm=False,
        swing="fast",
        putt="slow",
        spin="back1",
    )
    assert fitted.removed == frozenset()
    assert not fitted.over_max


def test_a_required_bag_replaces_the_saved_bag():
    rules = ClubRules(required_bag=frozenset({Club.W2, Club.I7}))
    fitted = fit(SAVED, rules, abi=2)
    assert fitted.state.clubs == {"2W", "7I"}
    assert fitted.removed == frozenset()
    assert not fitted.over_max


def test_banned_clubs_are_removed_and_their_slots_left_empty():
    rules = ClubRules(banned=frozenset({Club.W1, Club.SW, Club.I9}))
    fitted = fit(SAVED, rules, abi=2)
    assert fitted.state.clubs == {"3W", "5I", "PW"}
    assert fitted.removed == {"1W", "SW"}
    assert not fitted.over_max


def test_a_bag_over_the_max_is_flagged_not_trimmed():
    fitted = fit(SAVED, ClubRules(max=5), abi=2)
    assert fitted.state.clubs == {"1W", "3W", "5I", "PW", "SW"}
    assert fitted.over_max
    assert not fit(SAVED, ClubRules(max=6), abi=2).over_max


def test_removing_banned_clubs_can_bring_a_bag_under_the_max():
    rules = ClubRules(max=5, banned=frozenset({Club.SW}))
    fitted = fit(SAVED, rules, abi=2)
    assert fitted.removed == {"SW"}
    assert not fitted.over_max


def test_the_vanilla_default_is_flagged_under_a_small_max():
    rules = ClubRules(max=10)
    assert DownloadState.default(rules).clubs == VANILLA_LABELS
    assert fit(SavedSettings(), rules, abi=2).over_max


def test_an_abi_one_seed_starts_swing_putt_and_spin_off():
    state = fit(SAVED, ClubRules(), abi=1).state
    assert (state.swing, state.putt, state.spin) == ("off", "off", "off")
    assert state.bgm is False
    assert state.player_name == "LUIGI"


def downloaded(**overrides) -> PlayerOptions:
    choices = {
        "player_name": "MARIO",
        "clubs": frozenset({Club.W2, Club.PW}),
        "bgm": True,
        "swing": SwingSpeed.MEDIUM,
        "putt": SwingSpeed.OFF,
        "spin": BallSpin.NORMAL,
    }
    return PlayerOptions(**(choices | overrides))


def test_a_download_under_default_rules_saves_every_setting():
    saved = to_save(downloaded(), ClubRules(), abi=2, previous=SAVED)
    assert saved == SavedSettings(
        player_name="MARIO",
        clubs=frozenset({Club.W2, Club.PW, Club.PT}),
        bgm=True,
        swing=SwingSpeed.MEDIUM,
        putt=SwingSpeed.OFF,
        spin=BallSpin.NORMAL,
    )


@pytest.mark.parametrize(
    "rules",
    [
        ClubRules(max=13),
        ClubRules(banned=frozenset({Club.SW})),
        ClubRules(required_bag=frozenset({Club.W2, Club.PW})),
    ],
)
def test_a_seed_with_club_rules_keeps_the_saved_bag(rules):
    saved = to_save(downloaded(), rules, abi=2, previous=SAVED)
    assert saved.clubs == SAVED.clubs
    assert saved.player_name == "MARIO"
    assert saved.swing == SwingSpeed.MEDIUM


def test_an_abi_one_seed_keeps_the_saved_swing_putt_and_spin():
    saved = to_save(
        downloaded(swing=SwingSpeed.OFF, spin=BallSpin.OFF),
        ClubRules(),
        abi=1,
        previous=SAVED,
    )
    assert (saved.swing, saved.putt, saved.spin) == (
        SAVED.swing,
        SAVED.putt,
        SAVED.spin,
    )
    assert saved.bgm is True
    assert saved.clubs == {Club.W2, Club.PW, Club.PT}


def test_saved_settings_round_trip_through_the_cookie():
    value = SAVED.to_cookie()
    assert set(value) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert SavedSettings.from_cookie(value) == SAVED


@pytest.mark.parametrize("value", [None, "", "garbage", "!!!", "e30", "bm90IGpzb24"])
def test_a_cookie_that_does_not_decode_reads_as_vanilla(value):
    assert SavedSettings.from_cookie(value) == SavedSettings()


def test_an_entry_supplies_name_and_clubs_and_leaves_the_rest():
    with_entry = SAVED.with_entry("TOAD", ("2W", "PW", "PT"))
    assert with_entry.player_name == "TOAD"
    assert with_entry.clubs == {Club.W2, Club.PW, Club.PT}
    assert (with_entry.bgm, with_entry.swing, with_entry.spin) == (
        SAVED.bgm,
        SAVED.swing,
        SAVED.spin,
    )
