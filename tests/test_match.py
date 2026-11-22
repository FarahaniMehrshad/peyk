import pytest

from peyk.text.match import fold, ranked, score


def test_fold():
    assert fold("كتابِ  عربي") == "کتاب عربی"
    assert fold("می\u200cخواهم") == "میخواهم"
    assert fold("ÉCOLE ۱۲") == "ecole 12"
    assert fold("آرش") == fold("ارش")


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("sar", "Sara M."), ("dev", "Peyk Developers"), ("سارا", "سارا محمدی"),
        ("محمدي", "سارا محمدی"), ("pkdev", "Peyk Developers"), ("develpers", "Peyk Developers"),
        ("m sara", "Sara M."), ("ellop", "Developers"), ("میخواهم", "می\u200cخواهم"),
    ],
)  # fmt: skip
def test_found(query, name):
    assert score(query, name)


@pytest.mark.parametrize(
    ("query", "name"), [("xyz", "Sara"), ("sara", "S"), ("reza", "سارا"), ("@sara", "Sara")]
)
def test_not_found(query, name):
    assert score(query, name) is None


def test_usernames():
    assert score("@mina", "Mina K.", "minak") and score("minak", "Other", "minak")
    assert score("@nak", "Mina K.", "minak") and score("@zed", "Zed", "minak") is None


def test_order():
    names = ["Old Sara", "Sara", "Rasarat", "Bob"]
    assert ranked("sara", names, lambda n: (n, "")) == ["Sara", "Old Sara", "Rasarat"]
    assert ranked("", names, lambda n: (n, "")) == names
