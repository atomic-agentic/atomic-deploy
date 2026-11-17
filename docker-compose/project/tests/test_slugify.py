import pytest

from compose_example import slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World!", "hello-world"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("Crème brûlée", "creme-brulee"),
        ("v2.0 release", "v2-0-release"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_separator() -> None:
    assert slugify("Hello World", separator="_") == "hello_world"
