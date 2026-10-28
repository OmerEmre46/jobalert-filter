from jobalert_filter.filter.rules import RuleEngine
from jobalert_filter.models import Job
from jobalert_filter.text import fold, fold_pattern


def job(title, company="Acme", location="İstanbul, Türkiye", workplace=""):
    return Job(job_id="1", title=title, url="u", company=company, location=location,
               workplace=workplace)


def test_fold_handles_turkish_letters():
    assert fold("İSTANBUL Kıdemli Şirket Çağrı Ölçüm Üniversite") == \
        "istanbul kidemli sirket cagri olcum universite"


def test_fold_pattern_keeps_escapes():
    assert fold_pattern(r"\D+Kıdemli\b") == r"\D+kidemli\b"


def test_senior_is_rejected(profile):
    result = RuleEngine(profile).evaluate(job("Senior Backend Developer"))
    assert result.rejected
    assert "Senior" in result.reasons[0]


def test_turkish_reject_term_matches_without_diacritics(profile):
    result = RuleEngine(profile).evaluate(job("KIDEMLİ Yazılım Geliştirici"))
    assert result.rejected
    assert "KIDEMLİ" in result.reasons[0]  # reasons quote the title as written


def test_field_reasons_keep_turkish_letters(profile):
    result = RuleEngine(profile).evaluate(job("Stajyer – Mobil Uygulama Geliştirici"))
    assert "Geliştirici" in result.reasons[-1]


def test_years_of_experience_rejected(profile):
    assert RuleEngine(profile).evaluate(job("Developer (5+ years)")).rejected


def test_intern_scores_high(profile):
    result = RuleEngine(profile).evaluate(job("Stajyer – Backend Developer"))
    assert not result.rejected
    assert result.score >= 80


def test_title_without_level_scores_lower(profile):
    engine = RuleEngine(profile)
    plain = engine.evaluate(job("Backend Developer"))
    intern = engine.evaluate(job("Backend Developer Intern"))
    assert not plain.rejected
    assert plain.score < intern.score


def test_require_level(profile):
    profile.rules.require_level = True
    assert RuleEngine(profile).evaluate(job("Backend Developer")).rejected


def test_location_outside_preferences_costs_points(profile):
    engine = RuleEngine(profile)
    home = engine.evaluate(job("Backend Intern"))
    away = engine.evaluate(job("Backend Intern", location="Tuzla"))
    assert not away.rejected
    assert away.score == home.score - 30
    assert "Tuzla" in away.reasons[-1]


def test_strict_location_rejects(profile):
    profile.rules.strict_location = True
    result = RuleEngine(profile).evaluate(job("Software Intern", location="Berlin, Germany"))
    assert result.rejected


def test_internship_outside_target_fields_is_not_a_match(profile):
    profile.rules.field_keywords += [r"\bai\b", "machine learning"]
    engine = RuleEngine(profile)
    lab = engine.evaluate(job("Laboratory Intern, TTH", location="Greater Istanbul"))
    ai = engine.evaluate(job("AI & Machine Learning Research Intern",
                             location="Greater Istanbul"))
    assert lab.score < profile.min_score - 15  # lands in "Elenen", not "Belki"
    assert ai.score >= profile.min_score
    assert "hedef alan" in lab.reasons[-1]


def test_remote_ignores_location(profile):
    result = RuleEngine(profile).evaluate(
        job("Software Intern", location="Berlin, Germany", workplace="remote"))
    assert not result.rejected


def test_disallowed_workplace(profile):
    profile.rules.allow_onsite = False
    assert RuleEngine(profile).evaluate(job("Software Intern", workplace="onsite")).rejected


def test_company_blocklist(profile):
    profile.rules.reject_companies = ["Acme"]
    assert RuleEngine(profile).evaluate(job("Software Intern", company="ACME")).rejected


def test_english_messages(profile):
    result = RuleEngine(profile, "en").evaluate(job("Senior Engineer"))
    assert result.reasons[0].startswith("Unwanted term")
