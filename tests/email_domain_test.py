from app.utils.email_domain import (
    MCD_EMAIL_DOMAIN,
    email_matches_domain,
    extract_email_domain,
    is_mcd_email,
)


def test_extract_and_match() -> None:
    assert extract_email_domain("Claire@MyCatholicDoctor.com") == MCD_EMAIL_DOMAIN
    assert email_matches_domain("np@nashua.mycatholicdoctor.com", MCD_EMAIL_DOMAIN)
    assert not email_matches_domain(
        "attacker@evil-mycatholicdoctor.com", MCD_EMAIL_DOMAIN
    )
    assert not email_matches_domain("doc@gmail.com", MCD_EMAIL_DOMAIN)
    assert is_mcd_email("lead@mycatholicdoctor.com")
    assert not is_mcd_email(None)
    assert not email_matches_domain("a@b.com", None)
