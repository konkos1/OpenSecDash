from app.locales import LOCALES


def translate(
    key: str,
    language: str = "en",
) -> str:

    locale = LOCALES.get(
        language,
        LOCALES["en"],
    )

    return locale.get(
        key,
        key,
    )
