"""Тесты разбора cookies для импорта сессии HH.ru."""

import json

import scraper


def test_parse_list_of_cookies():
    raw = [{"name": "hhtoken", "value": "abc", "domain": ".hh.ru", "path": "/"}]
    out = scraper.parse_cookies_payload(raw)
    assert len(out) == 1
    assert out[0]["name"] == "hhtoken"
    assert out[0]["value"] == "abc"


def test_parse_dict_with_cookies_key():
    raw = {"cookies": [{"name": "a", "value": "1"}]}
    out = scraper.parse_cookies_payload(raw)
    assert out[0]["name"] == "a"
    assert out[0]["domain"] == ".hh.ru"


def test_parse_dict_mapping():
    raw = {"hhtoken": "xyz"}
    out = scraper.parse_cookies_payload(raw)
    assert out[0]["name"] == "hhtoken"
    assert out[0]["value"] == "xyz"


def test_parse_bytes_input():
    raw = json.dumps([{"name": "a", "value": "1"}]).encode("utf-8")
    out = scraper.parse_cookies_payload(raw)
    assert out[0]["name"] == "a"


def test_samesite_none_forces_secure():
    out = scraper._convert_cookies([
        {"name": "a", "value": "1", "sameSite": "no_restriction"}
    ])
    assert out[0]["sameSite"] == "None"
    assert out[0]["secure"] is True


def test_session_cookie_expires_minus_one():
    out = scraper._convert_cookies([{"name": "a", "value": "1", "session": True}])
    assert out[0]["expires"] == -1


def test_skips_cookies_without_name():
    out = scraper._convert_cookies([{"value": "1"}, {"name": "ok", "value": "2"}])
    assert len(out) == 1
    assert out[0]["name"] == "ok"


def test_invalid_payload_raises():
    try:
        scraper.parse_cookies_payload(12345)
        assert False, "ожидали ValueError"
    except ValueError:
        pass
