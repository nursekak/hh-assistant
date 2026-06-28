import experience


class TestParseRequiredYears:
    def test_no_experience(self):
        assert experience.parse_required_years("Без опыта") == 0.0
        assert experience.parse_required_years("Опыт не требуется") == 0.0

    def test_ranges(self):
        assert experience.parse_required_years("От 1 года до 3 лет") == 1.0
        assert experience.parse_required_years("От 3 до 6 лет") == 3.0

    def test_more_than(self):
        assert experience.parse_required_years("Более 6 лет") == 6.0

    def test_empty(self):
        assert experience.parse_required_years("") is None
        assert experience.parse_required_years("офис, кофе, печеньки") is None


class TestParseResumeYears:
    def test_years_and_months(self):
        assert experience.parse_resume_years("Опыт работы: 2 года 3 месяца") == 2.25

    def test_only_years(self):
        assert experience.parse_resume_years("Опыт работы 5 лет") == 5.0

    def test_only_months(self):
        assert experience.parse_resume_years("Опыт работы: 6 месяцев") == 0.5

    def test_missing(self):
        assert experience.parse_resume_years("Просто текст без стажа") is None
        assert experience.parse_resume_years("") is None


class TestIsExperienceOk:
    def test_skip_when_required_higher(self):
        # резюме ~2.25, требуется от 3 — не проходит
        assert experience.is_experience_ok(3.0, 2.25, tolerance=0.5) is False

    def test_pass_within_range(self):
        assert experience.is_experience_ok(1.0, 2.25, tolerance=0.5) is True

    def test_pass_within_tolerance(self):
        # требуется 3, резюме 2.6 + допуск 0.5 = 3.1 >= 3 → проходит
        assert experience.is_experience_ok(3.0, 2.6, tolerance=0.5) is True

    def test_no_experience_required_always_ok(self):
        assert experience.is_experience_ok(0.0, 0.0) is True

    def test_unknown_values_not_filtered(self):
        assert experience.is_experience_ok(None, 2.0) is True
        assert experience.is_experience_ok(5.0, None) is True
