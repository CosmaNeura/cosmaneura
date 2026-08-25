from app.utils.email_domain import (
    MCD_EMAIL_DOMAIN,
    email_matches_domain,
    extract_email_domain,
    is_mcd_email,
    is_public_email_domain,
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


def test_public_email_domain_matching_is_exact_and_normalized() -> None:
    assert is_public_email_domain(" Gmail.COM ")
    assert is_public_email_domain("@outlook.com")
    assert not is_public_email_domain("clinic.example.com")
    assert not is_public_email_domain("clinic.gmail.com")
    assert not is_public_email_domain(None)
